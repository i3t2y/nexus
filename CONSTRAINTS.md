# CONSTRAINTS.md（NPC / hermes 硬约束 · 须注入 role prompt）

> 本文件是 nexus 自我改进闭环的「不可违反约束」。任何 NPC（含 CNB CodeBuddy）或 hermes 派发的子任务，都必须先加载本文件。违反任一条即中止。

## 1. 单一真相源
- 配置真相源：Neon 四表 + hermes_mem0 + MEMORY.md + skills。
- 评测真相源：`eval/score.py` 及其断言（dataset 分数）。评测仪不得被优化过程修改。

## 2. 保护内核（禁止触及）
以下任何一项都**禁止 NPC / 子 agent 写入或修改**：
- 派发通路（task_queue → 本机桥 → CNB 的线路）
- Neon（四表 / DSN）
- R2 快照桶
- mem0 向量记忆
- 任意 PSK / token / secret
- 仓库根 `.cnb.yml` 与 CI 配置

原因：NPC 能改 `.cnb.yml` 等于能自提权；能改派发通路/Neon/R2/mem0 等于能动生产真相源。

## 3. 评测仪不可自改
`eval/score.py`、`eval/harness.py`、`eval/loop.py` 只能由人类修改。优化过程只能改「被评估的配置/代码」，不能改「测量工具」。

## 4. 存储与同步边界
- **GitHub `i3t2y/nexus` = 生产库**，NPC / hermes 不直写；代码经 CNB 打磨后由 CI 同步投产。
- **CNB `nexus.zen/nexus` = 开发/测试库**，NPC 回写、代码在此打磨。
- 同步方向：CNB → GitHub 单向（CI `.cnb.yml` 兜底每 15 分钟）。

## 5. 限地 / 限权 / 限时
- 限地：默认单仓（`nexus.zen/nexus`），不跨仓操作。
- 限权：NPC 默认只读（读代码+评论）；写/推/PR 需显式工作模式且 ≤ Developer。
- 限时：流水线销毁即止；无长驻进程。

## 6. 缺依赖即停
需要密钥/配置（n-omn PSK、hermes 路由 prompt、Neon DSN 等）而未提供时，停下向用户索要，不得猜测、硬编码或跳过。
