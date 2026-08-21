## Context

动机见 `proposal.md - Why`；行为契约见 `specs/`。这里只记录塑造实现方案的既有约束（均已在上游 `v0.1.179` 源码中核实）：

- **上游打包已完全参数化**。`.goreleaser.yaml` 的 `release.github.owner/name` 直接读 `GITHUB_REPO_OWNER` / `GITHUB_REPO_NAME`，GHCR 镜像读 `GITHUB_REPO_OWNER_LOWER`。这意味着不需要改上游任何构建配置，就能把产物发到别的仓库。
- **该配置还引用了另外两个必需环境变量**：`TAG_MESSAGE`（拼进 Release 正文）和 `DOCKERHUB_USERNAME`（用 `"skip"` 作为跳过 DockerHub 推送的哨兵值）。任一缺失都会让模板渲染失败。
- **版本号解析只认三段纯数字**（`update_service.go` 的 `parseVersion`，逐段 `strconv.Atoi`，失败记 0）。`v0.1.179-nobrand` 会被解析成 `0.1.0`。
- **版本号来自编译期内嵌文件** `backend/cmd/server/VERSION`，而仓库内该文件滞后一个版本（上游靠发布后的回填 job 同步）。
- **前端开启了 `noUnusedLocals`**，构建命令为 `vue-tsc -b && vite build`：删掉链接却留下常量，构建直接失败。
- **前端产物内嵌进二进制**：vite 输出到 `backend/internal/web/dist`，Go 侧用 `-tags=embed` 打包，因此前端必须先于 Go 构建完成。
- **下载域名白名单为 `github.com` / `objects.githubusercontent.com`**，本仓库同在 github.com，无需放宽。

## Goals / Non-Goals

**Goals:**

- 用最小改动面（3 处前端删除 + 1 个后端常量）达成去品牌与更新回流。
- 让"上游重构导致补丁失效"必然表现为流水线红灯，而不是产出一个漏改的版本。
- 不引入自研打包逻辑，最大程度复用上游已验证的 GoReleaser 配置。

**Non-Goals:**

- 不追求"每个上游版本都在本仓库有对应产物"。流水线只跟 `releases/latest`，两次检测之间被跳过的中间版本不回补（可手动 dispatch 补）。
- 不追求 Release 正文与上游逐字一致。
- 不引入通知（Telegram 等）与 DockerHub 推送。

## Decisions

### D1: 瘦仓库 + 构建期临时 clone 上游

本仓库只存流水线与补丁脚本；CI 内把上游按 tag clone 到独立目录，改写后就地构建。

- **替代方案**：镜像仓库（把改写后的上游源码提交进本仓库并打 tag）。优点是 tag 指向真实源码、更贴合 LGPL 的源码提供义务；缺点是每版一次全树提交、上游改动大时需要人工解冲突。
- **取舍**：补丁面只有 4 处，用"补丁脚本 + 上游 tag"就足以完整描述差异，不值得为此背上源码同步的维护成本。LGPL 的源码义务通过在仓库中公开补丁脚本、并在 Release 正文标注对应的上游 tag 与 commit 来满足。

### D2: 复用上游 `.goreleaser.yaml`，只靠环境变量转向

GoReleaser 在 clone 出来的上游目录内运行，配置文件原样使用，注入：

| 环境变量 | 值 | 作用 |
|---|---|---|
| `GITHUB_REPO_OWNER` / `GITHUB_REPO_NAME` | 本仓库 | Release 发布目标 |
| `GITHUB_REPO_OWNER_LOWER` | 本仓库 owner 小写 | GHCR 命名空间 |
| `DOCKERHUB_USERNAME` | `skip` | 跳过 DockerHub 推送 |
| `TAG_MESSAGE` | 上游 Release 正文 + 本仓库说明前言 | Release 正文 |
| `GITHUB_TOKEN` | 本仓库内置 token | 发布 Release |

- 因工作副本被改写过（dirty tree），必须带 `--skip=validate`；上游自己的发布流程也是这么跑的。
- `DOCKERHUB_USERNAME=skip` 只阻止推送，DockerHub 那两个镜像仍会本地构建。多花几分钟，但换来"不改上游构建配置"，值得。

### D3: 发布 tag 与上游同名，正文标注真实来源

受 D 段 Context 中的版本解析限制，tag 只能是 `v0.1.179` 这类纯上游形态。由此产生一个副作用：GoReleaser 未设置 `target_commitish`（默认为空），GitHub 在创建 Release 时若该 tag 不存在，会把 tag 建在本仓库默认分支 HEAD 上——也就是说 **tag 指向的是流水线代码，不是被构建的源码**。

- **缓解**：在 Release 正文前言里写明"基于上游 `<tag>` / `<commit>` 重打包，改动内容见本仓库补丁脚本"，让 tag 只承担版本标识与幂等键的职责。
- **副作用二**：上游配置的 Release footer 里有一条 `raw.githubusercontent.com/<owner>/<repo>/main/deploy/install.sh` 的一键安装命令，在本仓库会 404（我们不携带 `deploy/`）。同样在前言中声明该安装方式不适用于本仓库，不为此去改上游配置。

### D4: 带断言的脚本改写，而非 `.patch` 文件

改写用脚本按"锚点字符串"定位，每处遵循 `改前必须命中 → 执行 → 改后必须清零` 的三段式。

- **替代方案**：`git apply` 补丁文件。精确，但带上下文行，上游几乎每天发版，任何邻近行改动都会让补丁作废。
- **取舍**：脚本以 URL 常量、组件锚点这类稳定文本为准，对无关重构免疫；断言保证它退化时是显式失败。
- 后置校验在两个层面做：源码层（三处入口锚点清零、后端常量已改）与构建产物层（前端 `dist` 中不再出现被删入口的目标地址）。产物层校验需要注意：管理后台保留的合规/支付文档链接同样包含上游仓库地址，所以断言必须针对具体入口，不能笼统 grep 上游组织名。

### D5: 单 job 顺序执行，而非上游的三 job 拆分

上游拆成 `update-version` / `build-frontend` / `release` 三个 job 是为了并行与产物复用；我们的前端构建产物必须落在同一份被改写过的工作副本里，跨 job 传递反而更绕。

顺序：

```
检测上游 latest tag
      │
      ▼
本仓库已有同名 tag/Release？──是──▶ 结束（幂等，成功）
      │ 否
      ▼
clone 上游 @ tag ──▶ 执行改写脚本（含断言）
      │
      ▼
写入 VERSION 文件（tag 去 v 前缀）
      │
      ▼
setup node/pnpm ──▶ 前端构建 ──▶ 产物层断言
      │                （输出到 backend/internal/web/dist）
      ▼
setup go + buildx/QEMU + 登录 GHCR
      │
      ▼
goreleaser release --clean --skip=validate
      │
      ▼
Release（5 归档 + checksums）+ GHCR 多架构镜像
```

工具链的 setup 步骤必须排在 clone 之后：pnpm / Go 的依赖缓存键要读上游目录里的 `pnpm-lock.yaml` 与 `go.sum`，clone 之前那些路径还不存在。

### D6: 幂等键 = 本仓库是否已存在该版本的 Release

以本仓库 Release/tag 是否存在作为唯一判据。定时运行只在"上游 latest 尚未发布过"时才继续。

**已知的重试语义缺口**：GoReleaser 默认 `mode: keep-existing` 且未开 `replace_existing_artifacts`，若某次运行在上传产物中途失败，本仓库会留下一个残缺 Release——后续定时运行会因"已存在"而跳过，手动重跑则会在重复上传时撞 422。**操作约定**：遇到残缺发布，先手动删除该 Release 与 tag，再用 `workflow_dispatch` 重跑。

### D7: 镜像标签与构建形态沿用上游默认

- **GHCR 浮动标签保留**：沿用上游配置推送的 `latest`、`<major>`、`<major>.<minor>` 三个浮动标签，外加精确版本标签。这样下游 `docker pull ghcr.io/<owner>/sub2api:latest` 可以持续跟版，不必每次改 compose 文件。
- **始终全平台构建**：不使用上游的 `SIMPLE_RELEASE` 精简模式（该模式只出 x86_64 镜像、跳过其余产物）。即便上游某次自己走了精简模式，本仓库仍产出完整的 5 个平台归档与多架构镜像 —— 下游实例的在线更新依赖平台归档，缺哪个平台，那个平台的实例就失去更新能力。

**由浮动标签引出的操作约定**：手动 dispatch 回补历史版本时，GoReleaser 同样会把 `latest` 指向那个历史版本。回补完成后需要再手动跑一次当前最新版本，把 `latest` 拨回来。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 上游重构导致某处锚点失效，产出漏改版本 | 三段式断言 + 失败即中止；漏改在物理上无法发布 |
| 上游改动大时补丁长期失效，跟版中断 | 失败即红灯可见；每 6 小时重试一次，修复后自动跟上 |
| 部分失败留下残缺 Release，堵住后续自动发布 | 见 D6 操作约定：删 Release + tag 后手动 dispatch |
| tag 指向默认分支 HEAD，与实际构建源码无关 | 正文标注上游 tag/commit；tag 仅作版本标识 |
| 两次检测之间上游连发多版，中间版本被跳过 | 接受：实例可直接跳到最新版；需要回补时手动 dispatch |
| 上游 GitHub API 匿名调用限流 | 检测步骤使用内置 `GITHUB_TOKEN` 认证调用 |
| arm64 镜像走 QEMU，整条流水线耗时可观（预计 30 分钟量级） | 公共仓库 Actions 免费；6 小时一次的频率下无额度压力 |
| 下游实例的更新能力完全绑定本流水线，停摆即无更新 | 属于既定取舍；每个 Release 的正文都标注了对应的上游 tag 与 commit，需要时可据此回到上游 |
| LGPL-3.0 的源码提供义务 | 补丁脚本公开于本仓库；归档内 `LICENSE` 由 GoReleaser 原样携带；Release 正文标注上游出处 |
| 回补历史版本会把 GHCR `latest` 拨回旧版 | 见 D7 操作约定：回补后重跑一次最新版本 |

## Migration Plan

1. 先用 `workflow_dispatch` 指定当前上游最新 tag 跑一次，验证全链路。
2. 人工验收：Release 含 5 个归档 + `checksums.txt`；GHCR 多架构镜像可拉取；启动实例后确认首页页脚、用量页页脚、管理员下拉三处 GitHub 入口消失，合规/支付文档链接仍可用；触发一次"检查更新"确认请求落到本仓库。
3. 验收通过后开启 6 小时定时。
4. **回滚**：停用定时触发即可，已发布的 Release 与镜像不受影响；需要撤回某个版本时删除对应 Release 与 tag。

