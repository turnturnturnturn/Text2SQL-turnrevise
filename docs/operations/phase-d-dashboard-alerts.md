# 阶段 D Dashboard 与告警

## Dashboard

Prometheus 只使用低基数标签：route、risk、model、version、mode、status、stage、decision。面板应至少包括：阶段与总耗时 P50/P95、guard 决策、澄清创建/成功/重放/过期、context token、grounding coverage、rollout cohort，以及非终态 run 数量。

禁止把 user ID、run ID、原问题、prompt、SQL、数据库值或 memory 内容用作 label。排障时通过受权的 `/api/runs/{id}/trace` 查询脱敏事件序列。

## 告警与动作

| 告警 | 条件 | 自动动作 |
|---|---|---|
| Safety/Cross-user | 任意一次 | 全局降至 shadow |
| Error ratio | 15 分钟达到稳定基线 2 倍且样本不少于 20 | 降级对应 cohort |
| Latency | 简单/复杂 P95 连续 30 分钟超过 25/50 秒 | 降级对应 route |
| Provenance | 缺失率大于 0 | 停止扩量 |
| Terminal closure | 闭合率低于 100% | 停止扩量 |

后台监测每 60 秒运行并使用 PostgreSQL advisory lock。每次自动动作必须产生新 policy version、`rollout_decisions` 记录、脱敏 trace 和业务审计。
