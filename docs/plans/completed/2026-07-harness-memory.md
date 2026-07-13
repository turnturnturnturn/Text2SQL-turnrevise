# Harness 与持久记忆实施记录

状态：代码与数据库迁移已完成，真实 Qwen 全量质量评测保留为后续运行项。

## 已交付

- Git 基线与三个独立 worktree 子任务；主分支按状态层、运行层、评测层顺序集成。
- PostgreSQL `agent_state` 最小权限 Schema、持久会话、运行轨迹、候选/确认记忆和管理 API。
- RequestHarness 状态机、统一 run_id、超时、工具/纠错预算、启动遗留运行清理。
- 最近六轮上下文、抽取式早期摘要、confirmed-only BGE + RRF 记忆召回。
- Harness 指标聚合、AGENTS 协作规范和统一验证脚本。

## 验证

- Python：77 项通过。
- 数据库迁移连续执行两次成功，验证状态账号拥有 `agent_state.memories` INSERT，但无 `public.orders` SELECT/INSERT。
- Docker 最新 Agent 成功构建并启动；真实失败轨迹正确记录为 `FAILED / TypeError`，用于发现并修复嵌套 `super()` 问题。
- Java 测试和完整本地 Qwen 回归由 `scripts/verify-all.sh` 与 `RUN_E2E=1` 入口执行，指标不预写。

## 后续

- 在完整本地模型运行窗口重新执行 20 条 live Text2SQL 与 Harness 轨迹评测。
- 数据量超过单用户 500 条后再评估 pgvector、异步摘要和记忆压缩。
