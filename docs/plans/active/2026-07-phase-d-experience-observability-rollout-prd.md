# Enterprise Database Copilot 阶段 D PRD：体验、观测与灰度发布

| 字段 | 内容 |
| --- | --- |
| 状态 | Draft v1.0，待评审 |
| 日期 | 2026-07-21 |
| 前置版本 | 阶段 A–C 已完成；分支 `feat/phase-c-context-harness` |
| 建议周期 | 3–4 周；按可独立验收的 4 个里程碑交付 |
| 默认发布模式 | `shadow`；任何真实用户流量进入 `enforce` 均须显式配置 |
| 北极星指标 | Trusted Resolution Rate（可信解决率） |

## 1. 背景与问题

阶段 A–C 已建立语义目录、Grounding/QueryPlan、Context Compiler、Harness 状态机、澄清卡、恢复边界和三态灰度。当前系统能给出可验证的内部证据，但仍缺少把这些证据变成用户可理解界面、把每次失败转成可定位信号、以及以可回滚方式进入真实流量的产品闭环。

目前的主要缺口：

- 用户只能看到结果或文本，无法快速核对“使用了什么指标、时间字段、值映射和 Join”。
- `run_id`、manifest、QueryPlan、guard 决策散落在多个内部接口，未形成单一脱敏 trace 和失败归因视图。
- 澄清选择已生成 child run，但客户端没有明确的“继续此选择”交互闭环。
- 现有评测以离线题集为主，缺少版本冻结、回放、差异比较和上线门槛。
- `shadow`、`enforce` 已存在，却没有按 tenant/用户群/风险等级逐步放量、自动回滚和运营手册。

研究信号支持把阶段 D 定义为“评测和交互的一等产品能力”：BIRD-Interact 将多轮澄清作为独立成功率衡量，且公开结果显示交互式任务仍很难；LiveSQLBench-Large 进一步加入大 Schema 与业务规则漂移。[BIRD](https://bird-bench.github.io/) OpenTelemetry 的 GenAI 语义约定已能标准化模型、token、工具和时延，但官方同时明确提示消息与工具内容采集有敏感信息风险，应默认关闭内容导出并在 Collector 侧过滤。[OpenTelemetry GenAI Observability](https://opentelemetry.io/blog/2026/genai-observability/)，[敏感数据处理](https://opentelemetry.io/docs/security/handling-sensitive-data/)

## 2. 目标与非目标

### 2.1 目标

1. 让每个可执行答案都有面向用户的、非思维链式的“答案依据”。
2. 让运维和开发可从同一个 `run_id` 定位慢、错、被拒绝、澄清或回滚的阶段与版本。
3. 让澄清选择能安全地续跑一个绑定 evidence 的只读 child run。
4. 建立可复现的 160 题发布评测、消融对比和线上质量门槛。
5. 实现按用户群、tenant、风险等级的渐进灰度与自动回滚。

### 2.2 非目标

- 不新增任意 SQL 写入、自动审批或业务写入恢复。
- 不暴露模型 CoT、原始 prompt、原始工具结果、PII、密钥或 JWT。
- 不在本阶段提供语义资产编辑后台；只消费已发布目录并展示版本。
- 不跨租户聚合原始 trace，也不把业务 SQL 或用户原文默认发送给第三方观测平台。
- 不承诺不同 SQL 方言支持；当前生产目标仍为 PostgreSQL。

## 3. 用户与关键场景

| 用户 | 场景 | 成功结果 |
| --- | --- | --- |
| 业务分析师 | 查询销售额后怀疑口径 | 30 秒内看到指标版本、时间字段、状态映射、Join 与数据更新时间。 |
| 业务分析师 | “销售时间”有多个来源 | 选择带来源的选项后，从 child run 获得结果，无需重新描述问题。 |
| 数据管理员 | 某指标升级后结果变动 | 以 asset/source hash 与版本筛选历史 run，看到影响范围而非猜测。 |
| 平台管理员 | P95 上升或 guard 拒绝激增 | 从面板定位模型、路由、阶段和版本，必要时关闭受影响 cohort 的 enforce。 |
| 研发人员 | 修改 linker/context 策略 | 对冻结题集运行回放，拿到与基线的逐题差异和消融报告。 |

## 4. 产品设计

### 4.1 答案依据抽屉（Evidence Drawer）

在现有结果卡片旁新增“答案依据”入口；默认折叠，任何用户无需看到模型思维链即可验证语义。

抽屉分为五个稳定区块：

1. **查询含义**：指标、维度、过滤、时间范围、排序、行数限制，来自 `ValidatedQueryPlan`。
2. **数据依据**：表、列、confirmed Join 路径、值映射、资产/规则版本和 source hash 前缀。
3. **可信状态**：链接置信度、已解决澄清、已知限制、是否使用 shadow/enforce。
4. **运行信息**：`run_id`、child/parent 关系、模型与 context policy 版本、耗时、数据更新时间。
5. **受控技术详情**：仅有 `admin` 可查看 SQL；其他角色只看脱敏 SQL 摘要或“无权限”。

展示数据只能来自 QueryPlan、GroundingSnapshot、ContextManifest、Guard 决策与确定性元数据。禁止显示 prompt、原始结果行、记忆正文、内部推理、候选 SQL 文本、连接串或工具参数。

若 run 无可展示的 evidence，抽屉显示“本次请求未启用 v2 证据链”，并给出模式与 run_id；不得伪造依据。

### 4.2 澄清续跑

`POST /api/runs/{parent_run_id}/clarify` 保持一次性回答语义，并扩展返回：`child_run_id`、`resume_token`（短时、单次、仅绑定用户/child/evidence hash）和可展示的已选项摘要。

客户端收到成功响应后自动用 `resume_token` 发起 `POST /api/runs/{child_run_id}/resume`。服务端须：

- 原子校验 child 属于当前用户、未启动、未过期且 parent 为 `NEEDS_CLARIFICATION`；
- 把原请求的不可变 hash、已选 evidence/source hash 和 QueryPlan 约束注入低权限 request/evidence 分区；
- 创建新的 SSE 流，但不复用 parent 的模型输出、工具调用或审批状态；
- 在成功启动后消费 token；第二次调用返回 `409`；取消、过期或越权分别返回确定性错误；
- 只允许 `READ_QUERY` child resume，任何 `BUSINESS_WRITE`/`APPROVAL` 均 fail closed。

客户端可选择“稍后继续”；token 到期后需重新发问，不能把过期选择静默套用。

### 4.3 脱敏 Trace 与指标

每个 run 输出一个内部 `RunTraceEnvelope`，并可按策略导出 OpenTelemetry trace/metrics。事件顺序固定：

```text
request_received → context_compiled → schema_linked → value_linked
→ plan_validated → sql_generated → guard_decided → sql_executed
→ result_verified | clarification_requested | run_failed | run_cancelled | run_completed
```

所有 span 必须带：`run_id`、`correlation_id`、`tenant_id`、`route`、`risk_level`、`model_id`、`prompt_version`、`schema_version`、`context_policy_version`、`grounding_mode`、`harness_mode` 和阶段耗时。遵循 GenAI 语义约定记录 token 与模型调用时延；对业务特有字段使用 `db_copilot.*` 前缀，避免把内部字段伪装为标准字段。

默认策略：

- `OTEL_EXPORTER` 未配置时仅写本地脱敏聚合和审计引用。
- 不设置 `gen_ai.input.messages`、`gen_ai.output.messages`、SQL 文本、用户原文、数据库值或 memory 内容。
- exporter 前执行 allowlist 属性构建；Collector 再执行 denylist/filter，作为第二道防线。
- trace 采样：所有失败、guard 拒绝、澄清和 P95 慢请求 100%；正常成功请求按 tenant 可配置的 1–10% 采样；聚合 metrics 不采样。
- 保留期：聚合 metrics 90 天、脱敏 trace 30 天、可回放 artifact 引用 30 天；到期删除不影响审计法定保留策略。

最小指标集：

| 指标 | 维度 | 告警用途 |
| --- | --- | --- |
| `db_copilot_run_duration_seconds` | route、risk、status、mode | P50/P95 与超时 |
| `db_copilot_stage_duration_seconds` | stage、model、mode | 慢点定位 |
| `db_copilot_guard_decisions_total` | guard_type、decision、mode | 安全拒绝漂移 |
| `db_copilot_clarifications_total` | reason、outcome | 澄清质量/放弃 |
| `db_copilot_context_tokens` | partition、prune_reason | 上下文膨胀 |
| `db_copilot_grounding_coverage` | asset_type、version | 链接缺口 |
| `db_copilot_rollout_decisions_total` | cohort、decision、reason | 灰度与回滚 |

### 4.4 回放评测与发布门槛

新增不可变 `evaluation_release`，每次发布冻结：题集版本、数据库快照 hash、语义目录版本、模型配置、prompt/context policy、feature flag 与随机种子（若适用）。报告必须同时给出 Oracle 指标与 live-model 指标，禁止混合。

160 题组成：

| 子集 | 数量 | 重点 |
| --- | ---: | --- |
| 现有回归 | 60 | 安全、SQL、审批、基础 Text2SQL |
| Memory/Context | 30 | 约束继承、冲突、隔离、压缩 |
| Grounding | 45 | schema/value/join/指标/负证据 |
| 交互与澄清 | 15 | 需要与不需要澄清、child resume、篡改/过期 |
| 复杂与回归 | 10 | 多表、规则漂移、P95、失败归因 |

每题输出 `run_id`、期望/实际状态、QueryPlan hash、evidence ids、失败阶段、guard 决策、延迟和脱敏 artifact 引用。新增变更必须与最近稳定 release 比较，并至少运行以下消融：

- `GROUNDING_V2_MODE=off` vs `shadow` vs `enforce`；
- `CONTEXT_HARNESS_V2_MODE=off` vs `shadow` vs `enforce`；
- 无 evidence drawer/trace 对答案路径的零影响验证；
- 当前模型与候选模型严格分离的结果比较。

### 4.5 灰度、回滚与运营

采用独立的 `RolloutPolicy`，匹配优先级为：显式用户 allow/deny → tenant → 角色 → route/risk → 全局默认。一个请求仅得到一个确定性决策，并将 policy version 写入 trace 与 evidence。

发布阶段：

1. **Shadow**：100% 内部流量记录差异；不改变答复。
2. **Internal enforce**：仅测试账号与管理员；只开放 LOW/MEDIUM 的只读路径。
3. **Canary 10%**：按稳定 `user_id` hash 分桶，避免同一用户来回切换。
4. **Canary 50%**：持续 7 天满足门槛后扩大。
5. **GA**：完成回滚演练、保留策略确认与安全评审后全量启用。

自动降级到 `shadow`（保留审计，不删除数据）的触发条件：

- 任一危险 SQL 成功执行或跨用户 evidence 泄露；
- 基线 60 题严格等价率低于 100%；
- 15 分钟内 guard/error 比率相对稳定基线增加 2 倍且样本数不少于 20；
- 连续 30 分钟简单/复杂 P95 分别超过 25/50 秒；
- trace provenance 缺失率大于 0，或非终态闭合率低于 100%。

安全事件触发时，除自动降级外还须分页通知值班人；性能事件只自动降级对应 cohort。任何自动操作不得改写业务数据。

## 5. 数据、接口与权限

新增或扩展的持久化对象：

- `agent_state.run_traces`：脱敏 trace envelope 与采样决策；不存原始内容。
- `agent_state.rollout_policies`、`agent_state.rollout_decisions`：版本化策略与逐 run 决策。
- `agent_state.evaluation_releases`、`agent_state.evaluation_cases`、`agent_state.evaluation_results`：冻结评测与逐题结果。
- `agent_state.clarification_resume_tokens`：仅保存 token hash、child/user/evidence 绑定、过期/消费状态。

`copilot_agent_state` 仅能读写上述状态表；`copilot_readonly` 无权限。所有展示/trace/evaluation API 使用 run 所属用户或 admin 鉴权，且再按 tenant 过滤。任何 API 返回中均不得含原始 prompt、SQL（非 admin）、结果行、隐私字段或 token 明文。

新增 API：

| 接口 | 权限 | 行为 |
| --- | --- | --- |
| `GET /api/runs/{id}/evidence` | owner/admin | 返回抽屉所需脱敏证据模型。 |
| `POST /api/runs/{id}/clarify` | owner | 返回一次性 child 与 resume token。 |
| `POST /api/runs/{id}/resume` | owner | 消费 token，启动绑定 child 的 SSE 续跑。 |
| `GET /api/runs/{id}/trace` | owner/admin | 返回脱敏事件序列与版本，不返回原文。 |
| `GET /api/ops/rollouts` | admin | 查看策略、cohort 与降级原因。 |
| `POST /api/ops/rollouts/{policy}/rollback` | admin | 显式降级到 shadow，写审计。 |

现有 SSE wire format 不变；Evidence Drawer 使用后续 REST 请求加载，避免增大聊天事件载荷。

## 6. 验收标准

### 6.1 安全与正确性

- 危险请求成功执行数为 0；业务写入/审批自动恢复数为 0。
- Evidence/trace/OTel payload 中原始 prompt、SQL、结果行、PII、密钥和 JWT 暴露数为 0。
- owner/admin 之外读取 evidence、trace、resume token 的成功数为 0。
- 澄清 token 重放、越权、过期、选择篡改均被拒绝；同一 child 仅启动一次。
- Evidence Drawer 所有显示 asset/source 均可在当前或历史版本中解析；无法解析时明确标红，不显示猜测内容。

### 6.2 质量与性能

- 160 题 release 报告完整率 100%，并可按失败阶段、模式、模型和题集子集筛选。
- Trusted Resolution Rate ≥ 80%；现有 60 题严格等价率 100%。
- 必要澄清识别率 ≥ 85%，非必要澄清率 ≤ 15%，已回答澄清 child 成功续跑率 ≥ 95%。
- trace 可将 ≥ 99% 的失败归到一个明确阶段；所有运行都有脱敏 `run_id` 关联。
- 简单只读查询 P95 ≤ 25 秒；复杂验证查询 P95 ≤ 50 秒。
- shadow 与 off 答复等价率 100%；关闭 rollout 后下一个新请求恢复稳定路径。

### 6.3 可运维性

- 每项 rollout 决策可重现：同一 policy/version/user/risk 得到同一 cohort。
- 自动回滚演练在非生产配置中通过，且不删除迁移数据。
- Collector/exporter 不可用时：shadow 不阻断答复；enforce 只在本地 trace 持久化也失败时 fail closed，并记录明确失败类型。

## 7. 实施顺序

1. 定义脱敏 telemetry contract、run trace envelope、属性 allowlist 和 migration；先做无 exporter 的本地持久化测试。
2. 实现 evidence API 和 Drawer 数据模型；以现有 QueryPlan/Grounding/Manifest 为唯一来源。
3. 完成 clarification resume token、child 绑定和 SSE 续跑；完成并发、过期、重放和跨用户测试。
4. 实现 release evaluator、160 题冻结清单、差异报告和消融命令。
5. 实现 RolloutPolicy、稳定分桶、管理员控制面、自动降级与演练脚本。
6. 接入 OpenTelemetry/Prometheus exporter，完成 dashboard、告警、数据保留和运维手册。
7. 完成 internal → 10% canary 的发布演练；仅在全部门槛通过后归档本 PRD。

## 8. 风险与决策

| 风险 | 决策/缓解 |
| --- | --- |
| 遥测泄露敏感数据 | 双层 allowlist/filter、默认不采集内容、契约测试与采样审计。 |
| Drawer 成为“漂亮但不准确”的说明 | 仅消费结构化证据；每项必须可回链 hash/version。 |
| 澄清续跑改变原问题语义 | token 绑定 parent/child/user/evidence hash；不复用 parent 输出。 |
| 灰度使同一用户体验抖动 | 使用稳定 user hash 分桶和显式 policy version。 |
| 160 题指标虚高 | 冻结数据库/目录/配置；Oracle 与 live-model 报告分开；保留逐题结果。 |
| OTel 规范仍演进 | 记录 `schema_url`/instrumentation version；自定义字段使用命名空间，不依赖实验字段的语义稳定性。 |

## 9. 发布与回滚说明

所有新增能力均通过独立开关启用：`EVIDENCE_DRAWER_MODE`、`OTEL_MODE`、`CLARIFICATION_RESUME_MODE`、`ROLLOUT_POLICY_MODE`。默认 `off` 或 `shadow`，不改变现有 SSE、JWT、审批和只读 SQL 行为。

回滚顺序：先将 cohort 强制降为 `shadow`，再关闭对应 UI/telemetry exporter；保留 run、evidence、trace 与评测数据用于审计。禁止通过删除 migration 或重写历史 run 实现回滚。

