# nexus 双仓同步架构（CNB 主 / GitHub 镜像）

> 建立日期：2026-09-06
> 适用仓库：`nexus.zen/nexus`（CNB，开发主仓） ↔ `i3t2y/nexus`（GitHub，只读镜像）

## 1. 为什么是单向镜像（CNB → GitHub）

- **NPC（CNB CodeBuddy）会把改动回写 `nexus.zen/nexus`**，因此 CNB 是真正的开发主仓。
- GitHub 仅作为公开的只读备份 / 对外展示窗口，**不在 GitHub 上直接开发**。
- 因此同步方向只能是 **CNB → GitHub 单向推送**；反向（GitHub→CNB）已被废弃（旧 `.cnb.yml` 的 `sync-from-github` 逻辑已移除）。
- 双向同步不建议：CNB 无入站 webhook，且 CNB 的 token 推不了 GitHub，需额外存 GitHub PAT 再写反向流水线，冲突面大。

## 2. 同步机制

定义在仓库根的 `.cnb.yml`：

| 触发 | 行为 |
|------|------|
| `main: push` | CNB main 有任意新提交（NPC 回写 / 人工提交）即把 CNB main `--force` 推到 GitHub main |
| `main: "crontab: */15 * * * *"` | 每 15 分钟兜底推送一次，防止漏推 |

推送用 `--force` 是有意的镜像语义（让 GitHub 严格等于 CNB）。由于只推 GitHub、不推 CNB，不会触发自身死循环。

## 3. 鉴权配置（一次性）

需要一个对 `i3t2y/nexus` 有 **repo 写权限** 的 GitHub classic PAT（`ghp_xxx`），存到 CNB：

**方式 1（推荐，最简单）— CNB 项目保密变量**
1. 打开 `nexus.zen/nexus` → 项目设置 → 流水线 → 变量
2. 新增变量 `GITHUB_TOKEN`，值填 PAT，勾选「保密」
3. 新增变量 `GITHUB_USER`，值填 `i3t2y`
4. `.cnb.yml` 已直接读取这两个环境变量，无需改文件

**方式 2 — 密钥仓库 imports**
1. 新建一个密钥仓库（如 `nexus.zen/secrets`），放 `secrets.yml`：
   ```yaml
   GITHUB_TOKEN: "ghp_xxx"
   GITHUB_USER: "i3t2y"
   ```
2. 在 `.cnb.yml` 的 `mirror-to-github` 任务下取消 `imports:` 注释，指向该文件 URL

## 4. 首次启用注意

- 启用前请确认 GitHub `i3t2y/nexus` **没有** CNB 没有的独特提交（曾经手动在 GitHub 改过）。若有，先备份或合并，否则首次 `--force` 会覆盖它们。
- 验证：在 CNB 提交一次，观察流水线日志是否出现 `🎉 已镜像到 GitHub`；再到 GitHub 仓库确认提交已出现。

## 5. 本目录内容

- `nexus自我改进方案-*.md`：nexus 自我改进闭环的方案与多轮审查报告
- `SYNC-双仓同步架构.md`：本文件
- `../eval/`：自我改进评估脚本（score.py / harness.py / loop.py + hermes_eval/）
