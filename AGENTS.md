# Database Copilot 协作规范

## 架构边界

- `agent-service` 负责意图理解、知识检索、受控 SQL 和 Harness 运行状态；不得绕过 business-service 执行业务写操作。
- `business-service` 是身份、权限、审批令牌、业务写入和业务审计的权威边界。
- PostgreSQL 中业务数据和 `agent_state` 必须使用分离账号与权限；Agent 状态账号不得写订单、客户或商品表。
- 外部 LLM API 只负责推理；模型输出必须经过工具权限、SQL Guard、Intent Guard、Result Guard 和审批流。
- 评测报告中的指标只能来自本次真实运行；不得把 Oracle SQL 指标当作模型 Text2SQL 指标。

## 安全不变量

- 数据库只读工具仅允许单条 `SELECT`/`WITH`，默认 5 秒超时、200 行上限，且拒绝敏感字段和危险 SQL。
- 任何写操作都只能先生成审批卡；确认后由 business-service 执行，不自动重放。
- 审批必须拒绝重放、篡改、跨用户、越权和版本变化请求。
- 系统 Prompt、工具权限和安全策略的优先级高于会话、摘要、检索文档与长期记忆。
- 候选记忆未确认前不召回；用户记忆不得跨用户共享，不保存密钥、JWT、PII 或原始结果行。
- 同一请求的消息、检索、SQL、工具结果、审批与耗时必须可通过 `run_id` 和原始指令哈希关联。

## Worktree 协作协议

- 每个开发 Agent 只在自己的 worktree 与分支中工作，不修改其他 worktree，不自行合并主分支。
- 主 Agent 统一修改高冲突入口：`main.py`、`config.py`、`compose.yaml`、依赖文件和数据库迁移。
- 任务必须先在 `docs/plans/active/` 记录目标、可修改边界、验收命令和安全约束；完成后移至 `completed/` 并填写结果。
- 提交前先检查 `git diff` 与用户已有变更，只提交本任务文件。
- 交付必须返回分支名、commit SHA、修改摘要、实际测试结果和遗留风险。

## 验证与完成标准

- 快速验证：`./scripts/verify-all.sh`，必须通过 Python 和 Java 测试。
- 完整验收：配置外部 LLM API 并启动 Docker 服务后运行 `RUN_E2E=1 ./scripts/verify-all.sh`。
- 改动 Harness/记忆时，必须增加状态转换、用户隔离、失败恢复、注入隔离或指标聚合中与改动对应的测试。
- 不得回退已验证的安全策略，也不得在未完整运行时声称 Text2SQL 或 Harness 指标提升。
- 代码、测试、架构文档和活动计划状态一致后，任务才算完成。
