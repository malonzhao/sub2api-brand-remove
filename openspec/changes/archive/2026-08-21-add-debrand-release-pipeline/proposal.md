## Why

上游 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) 的官方产物在前端会展示指向上游仓库的 GitHub 链接，后端的在线更新检测与下载也固定指向上游仓库。我们需要一份去除这些上游品牌入口、且自更新回流到本仓库的构建产物，并且要随上游发版（接近每天一次）持续跟进，靠人工重新打包不可维护。

本仓库因此定位为一条**流水线仓库（瘦仓库）**：自身不保存上游源码，只保存"检测 → 打补丁 → 构建 → 发布"的自动化流程与补丁脚本。

## What Changes

- 新增定时工作流：每 6 小时检测一次上游 `releases/latest`，若本仓库尚无同名 tag 则触发一次重打包；同时支持 `workflow_dispatch` 手动指定 tag 回补历史版本。
- 新增去品牌补丁脚本，在构建前对上游工作副本执行：
  - 删除前端三处指向上游仓库的可点击 GitHub 入口——首页页脚、API Key 用量页页脚、登录后右上角下拉导航（该项仅管理员可见）；连带删除随之失效的 `githubUrl` 常量。
  - 修正版本面板中提供给管理员复制执行的手动回滚命令：移除依赖上游 `deploy/install.sh` 的脚本回滚方式，容器回滚指引改用本仓库发布的 GHCR 镜像。原样保留会让管理员把实例换成上游构建。
  - 将后端在线更新的仓库常量由 `Wei-Shaw/sub2api` 改写为本仓库，使更新检测、版本回滚列表与产物下载全部回流到本仓库的 Release。
  - 每处改写前后都做断言：改前必须命中预期文本，改后必须清零，未命中即让流水线失败，不产出"漏改"的版本。
- 复用上游 `.goreleaser.yaml` 完成打包（其发布目标已由 `GITHUB_REPO_OWNER` / `GITHUB_REPO_NAME` 环境变量参数化），产出与上游同构的 5 个二进制归档 + `checksums.txt`，并推送多架构 GHCR 镜像至 `ghcr.io/<owner>/sub2api`。
- 发布 tag 与上游保持完全一致（如 `v0.1.179`），不加任何后缀。

### 明确不做（Non-goals）

- 不改动 `deploy/`、`docs/`、README 等非前后端代码；发布归档中随附的 `deploy/install.sh` 仍指向上游。
- 不改动管理后台内的功能性文档跳转（合规文档、支付接入文档）。这些链接指向上游仓库的 `docs/` 路径，本仓库不存在该路径，改指本仓库只会 404。
- 不改动 `pricing.remote_url` / `hash_url`（指向另一个仓库 `Wei-Shaw/model-price-repo` 的价卡数据源，改动会直接断掉价格同步）。
- 不镜像上游源码到本仓库，不维护长期分支。

## Capabilities

### New Capabilities

- `release-pipeline`: 上游版本检测、幂等判定、构建编排与产物发布（含 GHCR 镜像）的自动化行为。
- `debrand-patch`: 对上游工作副本执行的去品牌改写规则，及其命中断言与失败语义。

### Modified Capabilities

（无——本仓库此前没有任何 spec。）

## Impact

**本仓库新增**

- `.github/workflows/`：定时 + 手动触发的重打包工作流。
- 补丁脚本目录：前端链接删除与后端更新源改写。

**对上游工作副本的改动面（仅构建期，不落库）**

| 文件 | 改动 |
|---|---|
| `frontend/src/views/HomeView.vue` | 删除页脚 GitHub 链接与 `githubUrl` 常量 |
| `frontend/src/views/KeyUsageView.vue` | 同上 |
| `frontend/src/components/layout/AppHeader.vue` | 删除下拉导航中的 GitHub 项 |
| `frontend/src/components/common/VersionBadge.vue` | 删除脚本回滚方式，容器回滚镜像改为本仓库 GHCR 镜像 |
| `backend/internal/service/update_service.go` | `githubRepo` 常量改为本仓库 |
| `backend/cmd/server/VERSION` | 按发布 tag 写入版本号 |

**外部依赖与凭据**

- GitHub Actions 内置 `GITHUB_TOKEN`，需 `contents: write`（发布 Release）与 `packages: write`（推送 GHCR）。
- 构建工具链：Go（由上游 `go.mod` 固定）、Node 20 + pnpm 9、GoReleaser v2、Docker Buildx + QEMU。
- 上游 GitHub API 的匿名调用速率限制（检测步骤）。

**已知的行为性影响**

- 本仓库自动创建的 tag 会由 GitHub 指向默认分支 HEAD，而非实际构建所用的上游源码提交；tag 名仅用作版本标识与幂等键。
- 使用本产物的实例，其"检查更新/回滚"能力将完全依赖本仓库持续发版；一旦流水线停摆，实例不会再收到上游新版本提示。
