## 1. 仓库骨架

- [X] 1.1 建立目录结构：`.github/workflows/`（流水线）与 `scripts/`（改写脚本），确定上游 clone 目标目录名（如 `upstream/`）并加入 `.gitignore`

## 2. 去品牌改写脚本

- [X] 2.1 编写脚本入口：接受上游工作副本路径与目标仓库（`owner/repo`）两个参数，任一断言失败即以非零码退出并打印未命中的位置
- [X] 2.2 实现前端首页页脚 GitHub 入口的删除（`frontend/src/views/HomeView.vue`：删除锚点元素并删除随之失效的 `githubUrl` 常量）
- [X] 2.3 实现用量页页脚 GitHub 入口的删除（`frontend/src/views/KeyUsageView.vue`，同上）
- [X] 2.4 实现右上角下拉导航 GitHub 项的删除（`frontend/src/components/layout/AppHeader.vue`，含图标与文案的整块元素）
- [X] 2.4b 修正版本面板的手动回滚指引（`frontend/src/components/common/VersionBadge.vue`）：删除依赖上游 `install.sh` 的脚本回滚方式与其常量，容器回滚镜像改为本仓库 GHCR 镜像
- [X] 2.5 实现后端更新源常量改写（`backend/internal/service/update_service.go` 的 `githubRepo` → 目标仓库），并断言下载域名白名单未被改动
- [X] 2.6 为每处改写加上前置命中断言与后置清零断言；确认脚本不触碰合规文档、支付文档链接与 `pricing.remote_url`
- [X] 2.7 本地对 clone 下来的上游最新 tag 试跑脚本，确认四处全部命中且改写结果符合预期

## 3. 构建与发布流水线

- [X] 3.1 创建工作流骨架：6 小时定时 cron + `workflow_dispatch`（可选 tag 输入）、`contents: write` 与 `packages: write` 权限、并发组防重入
- [X] 3.2 实现版本检测步骤：认证调用上游 `releases/latest` 取 tag 与 Release 正文；手动触发时改用输入的 tag 并校验其在上游存在
- [X] 3.3 实现幂等判定步骤：本仓库已存在同名 tag/Release 则以成功状态提前结束，后续步骤全部门控
- [X] 3.4 按 tag clone 上游到工作目录，随后调用改写脚本（改写失败即中止）
- [X] 3.5 按 tag 写入 `backend/cmd/server/VERSION`（去 `v` 前缀）
- [X] 3.6 配置 Node 20 + pnpm 9（缓存键指向上游目录的 `pnpm-lock.yaml`），执行前端构建，并断言 `backend/internal/web/dist` 已生成
- [X] 3.7 增加产物层断言：`dist` 中不再出现被删除入口的上游地址、不出现游离的上游标识片段（运行时拼 URL 的写法），保留的文档链接仍在，且目标镜像标识存在
- [X] 3.8 配置 Go（`go-version-file` 指向上游 `backend/go.mod`）、QEMU、Buildx，并登录 GHCR
- [X] 3.9 组装 GoReleaser 所需环境变量（`GITHUB_REPO_OWNER`、`GITHUB_REPO_OWNER_LOWER`、`GITHUB_REPO_NAME`、`DOCKERHUB_USERNAME=skip`、`TAG_MESSAGE`、`GITHUB_TOKEN`），其中 `TAG_MESSAGE` = 本仓库前言（标注上游 tag/commit、改动摘要、一键安装命令不适用的说明）+ 上游 Release 正文
- [X] 3.10 执行 `goreleaser release --clean --skip=validate`，并在其后断言 Release 含 5 个平台归档与 `checksums.txt`

## 4. 首次验收

- [X] 4.1 手动 `workflow_dispatch` 指定当前上游最新 tag 跑一次全链路
- [X] 4.2 核对 Release 产物清单与文件命名规则同上游一致，`checksums.txt` 可用于校验
- [X] 4.3 核对 GHCR 镜像在 amd64 与 arm64 下均可拉取并启动，且 `latest` 浮动标签指向本次发布的版本
- [X] 4.4 启动实例人工验收：首页页脚、用量页页脚、管理员右上角下拉三处 GitHub 入口消失；合规文档与支付文档链接仍可用
- [X] 4.5 在实例中触发"检查更新"，确认请求指向本仓库、自报版本号与发布 tag 一致、且不误报有新版本；查看可回滚版本列表来自本仓库
- [X] 4.6 验收通过后启用定时触发，并观察至少一次上游发版后的自动跟进结果
- [X] 4.7 重新发布 v0.1.179（删除现有 Release 与 tag 后手动 dispatch），使已发布产物包含手动回滚指引的修正
