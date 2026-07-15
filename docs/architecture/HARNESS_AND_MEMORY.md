# Harness 与持久记忆架构

## 运行边界

`RequestHarness` 是 Vanna 工具循环外的确定性控制层。它负责运行状态、上下文编译、预算、权限、验证、记忆候选与可观测性；Qwen 只负责理解请求、选择工具和组织答案。Harness 不放宽 SQL 或业务写入权限。

请求流程为：

```text
RECEIVED -> CONTEXT_READY -> MODEL_RUNNING <-> TOOL_RUNNING
                                    |                |
                                    +----> VERIFYING +----> COMPLETED
                                                   \----> FAILED/CANCELLED
```

所有步骤共享不可变 `run_id`。默认预算是 8 次工具调用、2 次只读语义纠错和 120 秒总超时。写操作不自动重试。进程重启时，遗留的非终态运行标记为失败，不续接中断的模型流。

## 上下文编译

编译顺序固定为：系统安全规则、当前请求与角色、最近 6 轮对话、早期会话摘录、已确认用户记忆、Schema/指标/SQL 知识。会话超过 6 个用户轮次后，运行时生成不超过 1200 字符的抽取式早期摘录；原始会话仍持久化，后续可替换为模型摘要而不改变存储接口。

摘要、记忆与检索文档作为不可信参考数据包装，不能改变系统 Prompt、角色、工具白名单或审批规则。模型上下文不保存密钥或未脱敏工具输出。

## 持久化与记忆生命周期

PostgreSQL 的 `agent_state` Schema 保存：

- `conversations` / `messages`：用户归属和完整消息；
- `agent_runs` / `run_steps`：状态、预算、模型、检索模式、失败类型和耗时；
- `query_plans`：run 关联的计划 hash、验证状态、脱敏 grounding snapshot、证据 ids 与错误；
- `memories` / `memory_events`：类型、状态、来源、向量、版本与确认/拒绝/删除事件。

记忆类型为 `USER_PREFERENCE`、`BUSINESS_TERM`、`VERIFIED_QUERY` 和 `TOOL_PATTERN`。候选记忆由明确的“记住/以后按”请求或工具记忆接口产生；只有 `CONFIRMED` 状态参与召回。用户记忆严格按 `user_id` 隔离；管理员可以用“全局记住”创建 `GLOBAL` 业务术语候选，普通用户不能创建或确认全局候选。

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

## Grounding v2 与 QueryPlan

`search_schema_knowledge` 在 v2 中只读取 `semantic_catalog` 已发布、当前有效且非 Restricted 的资产；关系和值还必须通过端点/父字段的同等可见性门控。链接使用关键词与本地向量的 RRF，执行表优先、列反推表、候选表内选列、confirmed Join BFS 和受控值 Top-3；低于相关性阈值的候选不进入 evidence。candidate Join 只生成歧义标记，不能成为执行路径。Value Linking 不扫描业务表。

请求级上下文保存最近的 `GroundingSnapshot` 和 `ValidatedQueryPlan`，并随 Harness contextvar 一起释放，避免并发请求串扰。QueryPlan 的 confidence 由链接结果计算，模型不能提交自评分。`enforce` 模式下，SQL 工具在数据库执行前严格核对输出、指标公式与谓词、表、字段、值集合、时间窗口、粒度、排序、Limit 和 confirmed INNER Join；QueryPlan v1 对 CTE、UNION 与嵌套多作用域 SQL fail closed。无计划、证据持久化失败或任一不一致时均不执行数据库查询。`shadow` 的计划差异是 observational success，不阻断旧 SQL 路径；`off` 不注册计划工具并保留旧路径。

Harness 评测输入为真实运行轨迹，报告完成率、平均工具调用数、纠错成功率、Memory Recall@5、错误记忆暴露率、不合格记忆暴露率、错误记忆采用率、平均延迟和失败分类。专项离线题集只测真实检索路径的召回与暴露；“采用率”只有 Agent 轨迹明确提供 adopted memory ids 时才计算。缺少分母时输出 `null`/“无可用样本”，不视为 0% 或 100%。
