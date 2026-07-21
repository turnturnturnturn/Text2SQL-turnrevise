# 阶段 D 数据保留与脱敏

- `run_trace_events` 默认保留 30 天。
- 聚合指标默认保留 90 天。
- resume token 明文不落库；hash、过期和消费记录可在 parent/child run 审计窗口内保留。
- evaluation release、冻结配置 hash 与逐题脱敏结果作为发布审计长期保留；不保存原始题目、模型输出或结果行。
- rollout policy/decision 不覆盖历史版本。

每日清理任务应按 tenant 和时间分批删除过期 trace，避免长事务。清理前验证 release 审计和业务审计引用不依赖待删 payload。变更保留期通过 `TRACE_RETENTION_DAYS` 与 `METRIC_RETENTION_DAYS` 配置，并记录策略版本。

任何导出都只能使用 Trace allowlist。内容捕获永久关闭，包括 `gen_ai.input.messages`、`gen_ai.output.messages`、SQL、用户原文、数据库值和 memory 正文。
