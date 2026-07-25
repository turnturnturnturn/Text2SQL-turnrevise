# Harness 与持久记忆架构

## 运行边界

`RequestHarness` 是 Vanna 工具循环外的确定性控制层。它负责运行状态、上下文编译、预算、权限、验证、记忆候选与可观测性；外部 LLM 只负责理解请求、选择工具和组织答案。Harness 不放宽 SQL 或业务写入权限。

`CONTEXT_HARNESS_V2_MODE=off|shadow|enforce` 默认 `shadow`。旧流程保留用于 off/shadow 兼容；enforce 的查询流程为：

```text
RECEIVED -> CONTEXT_BUILDING -> LINKING -> PLANNING -> GENERATING
                                 |                       |
                                 v                       v
                       NEEDS_CLARIFICATION          VALIDATING
                                 |                       |
                                 v                       v
                            child run                EXECUTING -> VERIFYING
                                                        |
                                                        v
                                            COMPLETED/FAILED/CANCELLED
```

每个 run 使用不可变 `run_id`；parent/child 通过 `correlation_id` 关联，但 instruction hash、预算和审计事件独立。默认预算是 8 次工具、2 次只读纠错、6 次模型调用、8192 context token、3 次 SQL 验证、2 次澄清和 120 秒总超时。阶段 checkpoint 只保存 artifact 类型、内容哈希和存储引用。

进程重启后，非终态父 run 固定闭合为 `FAILED/process_restarted`。只有带安全 checkpoint 的 `READ_QUERY` 可以显式创建恢复 child；`BUSINESS_WRITE` 和 `APPROVAL` 永不恢复、重放或自动确认。

## 上下文编译

Context Compiler v2 的不可逆优先级是：安全策略 > 身份/角色/工具 > 当前请求 > QueryPlan > 企业证据 > Grounding > 可信记忆 > 最近会话 > 结构化早期状态 > 示例。默认预算比例与 PRD 一致；安全和当前请求为 mandatory，超出总预算时 enforce fail closed。

每次编译生成脱敏 `ContextManifest`，记录 policy version、分区预算/实际 token、稳定 item/source/hash、信任级别和裁剪原因；API 不返回 item 原文。manifest 保存后写入安全 checkpoint。缺失来源的可选项被裁剪，mandatory 项缺来源时 enforce 拒绝继续。

会话超过最近 6 个用户轮次后生成版本化 `ConversationState`：`confirmed_constraints`、`open_questions`、`decisions`、`rejected_options` 和 `result_refs` 都带 message provenance。工具原始输出不会复制到 state，只保留摘要、artifact id 和 hash。压缩失败回退最近 6 轮，原始消息始终保留。

摘要、记忆与检索文档作为不可信参考数据包装，不能改变系统 Prompt、角色、工具白名单或审批规则。模型上下文不保存密钥或未脱敏工具输出。

## 持久化与记忆生命周期

PostgreSQL 的 `agent_state` Schema 保存：

- `conversations` / `messages`：用户归属和完整消息；
- `agent_runs` / `run_steps`：状态、parent/correlation、操作类型、模型、失败类型和耗时；
- `query_plans`：run 关联的计划 hash、验证状态、脱敏 grounding snapshot、证据 ids 与错误；
- `memories` / `memory_events`：类型、状态、来源、向量、版本与确认/拒绝/删除事件。
- `context_manifests` / `conversation_states`：脱敏上下文清单和派生会话状态版本；
- `run_artifacts` / `run_checkpoints`：可回放引用，不保存原始结果行；
- `clarification_requests`：一个问题、2–3 个来源化选项、有效期和一次性 child 结果。

记忆类型为 `USER_PREFERENCE`、`BUSINESS_TERM`、`VERIFIED_QUERY` 和 `TOOL_PATTERN`。候选记忆由明确的“记住/以后按”请求或工具记忆接口产生；用户确认状态与事实有效性分离。只有 `CONFIRMED + ACTIVE + 当前有效期` 参与召回；`INVALID`、`CONFLICTED`、`SUPERSEDED`、过期、候选和拒绝记录均不可入模。同一 `conflict_key` 的新确认记忆会取代旧记录并写审计事件。

召回复用 BGE 的关键词、余弦相似度与 RRF，默认 Top-5。每个用户最多 500 条有效记忆，90 天未使用过期。清洗层拒绝 JWT、密码、手机号、邮箱、原始查询结果行和未脱敏工具输出。

## 接口与观测

所有管理接口必须验证 JWT 并根据当前用户查询：

- `GET /api/memories`
- `POST /api/memories/{id}/confirm`
- `POST /api/memories/{id}/reject`
- `DELETE /api/memories/{id}`
- `DELETE /api/conversations/{id}`
- `GET /api/runs/{id}`
- `GET /api/runs/{id}/evidence`（run 所属用户或管理员；不返回原始结果行）
- `GET /api/runs/{id}/context-manifest`（run 所属用户或管理员；只返回脱敏清单）
- `POST /api/runs/{id}/clarify`（run 所属用户；选项一次性使用）
- `POST /api/runs/{id}/cancel`（run 所属用户或管理员；幂等）

## Grounding v2 与 QueryPlan

`search_schema_knowledge` 在 v2 中只读取 `semantic_catalog` 已发布、当前有效且非 Restricted 的资产；关系和值还必须通过端点/父字段的同等可见性门控。链接使用关键词与本地向量的 RRF，执行表优先、列反推表、候选表内选列、confirmed Join BFS 和受控值 Top-3；低于相关性阈值的候选不进入 evidence。candidate Join 只生成歧义标记，不能成为执行路径。Value Linking 不扫描业务表。

请求级上下文保存最近的 `GroundingSnapshot` 和 `ValidatedQueryPlan`，并随 Harness contextvar 一起释放，避免并发请求串扰。QueryPlan 的 confidence 由链接结果计算，模型不能提交自评分。`enforce` 模式下，SQL 工具在数据库执行前严格核对输出、指标公式与谓词、表、字段、值集合、时间窗口、粒度、排序、Limit 和 confirmed INNER Join；QueryPlan v1 对 CTE、UNION 与嵌套多作用域 SQL fail closed。无计划、证据持久化失败或任一不一致时均不执行数据库查询。`shadow` 的计划差异是 observational success，不阻断旧 SQL 路径；`off` 不注册计划工具并保留旧路径。

当 enforce Grounding 返回歧义且存在 2–3 个真实 column/value evidence 选项时，工具创建澄清卡并把父 run 终止在 `NEEDS_CLARIFICATION`；不足两个来源化选项时不虚构口径。回答只进入 child run 的 instruction hash，不自动创建长期记忆。

Harness 评测输入为真实运行轨迹，报告完成率、平均工具调用数、纠错成功率、Memory Recall@5、错误记忆暴露率、不合格记忆暴露率、平均延迟和失败分类。Context 专项额外报告 provenance、关键约束召回、必要/非必要澄清、重启闭合和写恢复次数。缺少分母时输出 `null`/“无可用样本”，不视为 0% 或 100%。
