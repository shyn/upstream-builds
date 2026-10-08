# Upstream Builds

一个仓库集中跟踪、构建第三方开源项目。每天北京时间 / 新加坡时间 **08:17** 检查最新 SHA，仅在 SHA 与该目标的上次成功构建不同的时候打包。支持手动选项目、强制重建、Actions Artifact 和 GitHub prerelease。

## 已接入项目

| 目标 ID | 上游 | 分支 | 产物 | Runner |
| --- | --- | --- | --- | --- |
| `qingjian-macos-arm64` | [chenyukang/qingjian](https://github.com/chenyukang/qingjian) | `main` | macOS Apple Silicon `.pkg` | `macos-26` |

青简沿用上游数据校验和打包脚本。应用为 ad-hoc 签名，安装包没有 Apple Developer ID 签名或公证；首次安装可能需要在「系统设置 → 隐私与安全性」允许打开。这是非官方开发快照，不接入青简官方更新索引。

## 手动构建与下载

进入 **Actions → Build tracked upstreams → Run workflow**：

- `target`：`all` 检查全部项目，或填目标 ID，例如 `qingjian-macos-arm64`。
- `force`：默认 `false`，相同 SHA 跳过；勾选则强制重建。
- `publish_release`：勾选后在本仓库发布 prerelease。相同 SHA 已成功构建时，需要同时勾选 `force` 才会发布。

在运行页面的 **Artifacts** 下载产物。每份产物包含构建输出、同一提交的 `source.tar.gz`（含上游许可）、`build-info.json` 和 `SHA256SUMS`。解压后校验：

```bash
shasum -a 256 -c SHA256SUMS
```

青简还附带 `data.lock` 和签名状态说明。词库和模型由上游 `tools/release/data-fetch.sh` 从其指定的数据 Release 下载并按锁文件校验，Rust 版本取自被构建提交的 `rust-toolchain.toml`，Cargo 依赖由上游打包脚本通过 `--locked` 锁定。

若希望定时构建也发 Release，在 **Settings → Secrets and variables → Actions → Variables** 中设置 `PUBLISH_RELEASE=true`。删除或改为 `false` 可恢复仅上传 Artifact。默认无需 Secret。

Artifact 保留 **30 天**，可修改共享 workflow 的 `retention-days`。Release 不自动清理，标签含目标 ID、上游短 SHA、运行 ID 和尝试次数；失败可能留下草稿。根目录文件直接作为附件，若输出含子目录，还附上保留目录结构的完整 bundle。

## 添加更多上游

在 `upstreams.json` 的 `targets` 数组添加：

```json
{
  "id": "another-project-linux",
  "repository": "owner/repository",
  "branch": "main",
  "runner": "ubuntu-24.04",
  "build_script": "builds/another-project-linux.sh"
}
```

再新增对应脚本。脚本从**上游源码根目录**执行，依赖安装、构建、端到端检查由脚本负责，将要发布的文件放入 `$ARTIFACT_DIR`：

```bash
#!/usr/bin/env bash
set -euo pipefail
# 替换为项目实际构建和检查命令。
./build.sh
test -s dist/application.tar.gz
cp dist/application.tar.gz "$ARTIFACT_DIR/"
```

提交到本仓库默认分支即可，无须新建仓库或复制 workflow。目标 ID 必须唯一；同一项目不同平台使用不同 ID。清单校验允许 `ubuntu-24.04`、`ubuntu-26.04`、`macos-15`、`macos-26`、`windows-2025`，其他标签在 `plan.py` 明确添加。Windows 脚本也从 Bash 入口运行，可在其中启动 PowerShell。

当前针对公开上游；私有上游需额外配置跨仓库只读令牌，并调整检查和 checkout 认证。

## 策略与成功状态

定时频率在 `.github/workflows/build.yml` 修改，cron 使用 UTC：

| 频率 | cron |
| --- | --- |
| 每天北京时间 08:17（默认） | `17 0 * * *` |
| 每 6 小时 | `17 */6 * * *` |
| 每周一北京时间 08:17 | `17 0 * * 1` |

GitHub 定时事件可能排队延迟；公开仓库连续 60 天没有仓库活动时会自动停用定时 workflow。配置必须在默认分支。每次构建检查时的最新提交，不追溯期间的每个中间提交。

每个目标按仓库、分支与目标 ID 独立保存成功记录，存放在本仓库 annotated tag `build-state-<标识>` 的 JSON message 中，包括完整上游 SHA、运行链接与 Artifact 链接。tag 指向本仓库自身提交，无须同步上游提交。

仅在构建、包检查、Artifact 上传，以及启用时的 Release 发布全部成功后更新记录。失败后下次检查仍会尝试该 SHA。API 错误或损坏状态明确失败，不会当成没有更新。记录不依赖可能淘汰的 cache。Artifact 到期不会自动重建，需手动勾选 `force`；长期保存请启用 Release。

同一目标串行，正在构建的版本不因新运行而取消。不同目标最多并行两个，单个目标失败不会取消其他目标。GitHub 可能用新排队运行替换旧 pending 运行，因此不保证每个手动请求都执行。

旧任务重跑时，如果检查之后记录已被推进，会拒绝覆盖；选 **Re-run all jobs** 重新检查。修改脚本、runner 或发布策略不会改变上游 SHA，同 SHA 重建请用 `force`。

## 结构与权限

- `.github/workflows/build.yml`：定时 / 手动入口，清单校验与矩阵分发。
- `.github/workflows/build-upstream.yml`：共用的检查、构建、上传、发布流程。
- `.github/scripts/upstream.py`：读取和保存成功 SHA。
- `.github/scripts/package.py`：拒绝空输出，附上源码、构建凭证与校验清单。
- `builds/`：各项目的实际打包和包检查脚本。
- `verification/`：预先定义的失败场景、可重复的本地流程验证及报告。

检查和构建任务只有 `contents: read`，checkout 不保存凭据。发布与状态写入在独立 runner 执行，该任务有 `contents: write`，不运行上游代码。清单和打包脚本由维护者控制，没有 PR 自动执行入口。组织策略和 tag 保护必须允许发布任务管理 `build-state-*`。

构建凭证可追溯上游 SHA、构建配置提交及数据哈希，但不保证二进制逐字节可复现。

## 验证

```bash
python3 verification/scenarios.py
python3 verification/hub-scenarios.py
actionlint -shellcheck= .github/workflows/*.yml
```

第一组使用本地 HTTP 服务替代 GitHub API，覆盖成功状态、跳过、强制构建、错误响应和失败重试；第二组使用真实本地 Git 样例仓库验证清单计划和通用产物流程。两者不能替代真实 runner 打包、Artifact 上传及 Release 发布。实际验收运行链接与产物校验结果见 `verification/deployment.json`。

参考：[上游打包流程](https://github.com/chenyukang/qingjian/blob/main/.github/workflows/release.yml)、[GitHub 定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[Artifact 保留设置](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)、[Git references API](https://docs.github.com/en/rest/git/refs)。
