## Purpose

持续跟随上游 sub2api 的正式发版，自动产出一份经过去品牌改写的构建产物，并以与上游一致的版本号发布到本仓库，使下游实例可以从本仓库获取版本与更新。

## Requirements

### Requirement: 上游版本检测

流水线 SHALL 定时检测上游仓库的最新正式发布版本，并以其 tag 名作为本次发布的版本标识。检测频率 SHALL 为每 6 小时一次。

#### Scenario: 检测到上游新版本

- **WHEN** 定时触发且上游最新正式发布的 tag 在本仓库不存在
- **THEN** 流水线继续执行后续的拉取、改写、构建与发布流程

#### Scenario: 上游 API 不可用

- **WHEN** 定时触发但无法取得上游最新发布信息
- **THEN** 本次运行 MUST 失败并报错，且 MUST NOT 发布任何产物

### Requirement: 幂等发布

同一个上游版本 SHALL 只被发布一次。流水线 MUST 在构建开始前判定本仓库是否已存在同名 tag，已存在则跳过本次运行。

#### Scenario: 上游未发新版

- **WHEN** 定时触发且上游最新 tag 在本仓库已存在
- **THEN** 流水线以成功状态结束，MUST NOT 执行构建，MUST NOT 创建或覆盖任何 Release

#### Scenario: 同一小时内重复触发

- **WHEN** 同一上游版本在前一次运行中已成功发布，随后流水线再次被触发
- **THEN** 该次运行跳过构建，已发布的 Release 与镜像保持不变

### Requirement: 手动触发与版本回补

流水线 SHALL 支持手动触发，并允许指定一个上游 tag，以便回补历史版本或在补丁修复后重试失败的版本。

#### Scenario: 手动指定历史版本

- **WHEN** 操作者手动触发并指定一个存在于上游的历史 tag
- **THEN** 流水线按该 tag 拉取上游源码并执行完整的改写、构建与发布流程

#### Scenario: 手动指定不存在的 tag

- **WHEN** 操作者手动触发并指定一个上游不存在的 tag
- **THEN** 流水线 MUST 失败并报错，MUST NOT 发布任何产物

### Requirement: 版本号与上游一致

发布 tag SHALL 与上游 tag 完全一致，MUST NOT 追加任何后缀或前缀。二进制内嵌的版本号 MUST 与发布 tag 去掉 `v` 前缀后的值一致。

**理由**：下游实例的版本比较只解析三段纯数字，任何非数字后缀都会被解析为 `0`，导致实例永远认为存在新版本。

#### Scenario: 发布上游 v0.1.179

- **WHEN** 上游最新版本为 `v0.1.179`
- **THEN** 本仓库发布的 tag 为 `v0.1.179`，且运行中的实例自报版本为 `0.1.179`

#### Scenario: 实例已是最新版

- **WHEN** 实例运行的版本与本仓库最新发布版本相同
- **THEN** 实例的更新检测结果为"已是最新"，MUST NOT 提示存在新版本

### Requirement: 产物形态与上游同构

每次发布 SHALL 产出与上游同构的归档产物：linux/darwin 各 amd64 与 arm64 的 `tar.gz`、windows amd64 的 `zip`，以及一个 `checksums.txt`。归档文件名 MUST 保持上游命名规则，使下游实例的平台匹配与校验和验证逻辑无需改动即可工作。

平台覆盖 MUST 保持完整，不得因上游某次发布采用了精简产物形态而缩减。

#### Scenario: 实例执行在线更新

- **WHEN** 一台 linux/amd64 实例触发在线更新
- **THEN** 它能从本仓库最新 Release 中匹配到对应平台的归档、通过 `checksums.txt` 校验并完成二进制替换

#### Scenario: 构建产物缺失

- **WHEN** 构建完成但预期的归档或校验和文件未生成
- **THEN** 流水线 MUST 失败，MUST NOT 创建 Release

#### Scenario: 上游采用精简产物形态

- **WHEN** 上游某次发布只附带部分平台的产物
- **THEN** 本仓库仍 MUST 产出完整的五个平台归档与校验和文件

### Requirement: 容器镜像发布

每次发布 SHALL 同时推送多架构（amd64 + arm64）容器镜像至本仓库所属的 GitHub Container Registry 命名空间。镜像 SHALL 同时带有精确版本标签与 `latest` 浮动标签，使下游可以选择固定版本或持续跟版。

#### Scenario: 发布成功后拉取镜像

- **WHEN** 版本 `v0.1.179` 发布成功
- **THEN** 可从 GHCR 拉取到该版本标签的镜像，且在 amd64 与 arm64 主机上均可运行

#### Scenario: 按 latest 跟版

- **WHEN** 下游以 `latest` 标签部署，随后本仓库发布了更新的版本
- **THEN** 重新拉取 `latest` 得到的是该新版本镜像

### Requirement: 失败即中止

流水线的任一阶段失败时 SHALL 整体中止，MUST NOT 发布半成品。失败版本 MUST NOT 在本仓库留下同名 tag，以便修复后由下一次定时运行自动重试。

#### Scenario: 改写阶段失败

- **WHEN** 去品牌改写未能全部命中
- **THEN** 流水线中止，MUST NOT 构建、MUST NOT 发布，且该版本在下一次定时运行中重新进入待处理状态

#### Scenario: 构建阶段失败

- **WHEN** 前端或后端构建失败
- **THEN** 流水线中止且不产生 Release
