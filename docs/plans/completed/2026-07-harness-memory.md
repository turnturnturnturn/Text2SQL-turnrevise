# Harness 与持久记忆实施记录

状态：代码与数据库迁移已完成，真实 Qwen 全量质量评测保留为后续运行项。

## 已交付

- Git 基线与三个独立 worktree 子任务；主分支按状态层、运行层、评测层顺序集成。
- PostgreSQL `agent_state` 最小权限 Schema、持久会话、运行轨迹、候选/确认记忆和管理 API。
- RequestHarness 状态机、统一 run_id、超时、工具/纠错预算、启动遗留运行清理。
- 最近六轮上下文、抽取式早期摘要、confirmed-only BGE + RRF 记忆召回。
- Harness 指标聚合、AGENTS 协作规范和统一验证脚本。

## 验证

- Python：78 项通过。
- 数据库迁移连续执行两次成功，验证状态账号拥有 `agent_state.memories` INSERT，但无 `public.orders` SELECT/INSERT。
- Docker 最新 Agent 成功构建并启动；真实失败轨迹正确记录为 `FAILED / TypeError`，用于发现并修复嵌套 `super()` 问题。
- 完整 20 题本地 Qwen 回归：SQL 执行、严格结果等价、危险请求无执行和审计关联均为 100%；Harness 20/20 完成、3/3 纠错成功、平均 1.20 次工具调用。
- 真实 PostgreSQL 状态账号完成候选不可召回、确认后召回、重新连接恢复会话和测试数据清理冒烟。

## 后续

- 增加带 gold memory id 的独立记忆召回与错误记忆注入题集。
- 数据量超过单用户 500 条后再评估 pgvector、异步摘要和记忆压缩。
