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

编译顺序固定为：系统安全规则、当前请求与角色、最近 6 轮对话、会话摘要、已确认用户记忆、Schema/指标/SQL 知识。会话超过 12 条消息后产生不超过约 1200 tokens 的滚动摘要。

摘要、记忆与检索文档作为不可信参考数据包装，不能改变系统 Prompt、角色、工具白名单或审批规则。模型上下文不保存密钥或未脱敏工具输出。

## 持久化与记忆生命周期

PostgreSQL 的 `agent_state` Schema 保存：

- `conversations` / `messages`：用户归属、消息和滚动摘要；
- `agent_runs` / `run_steps`：状态、预算、模型、检索模式、失败类型和耗时；
- `memories` / `memory_events`：类型、状态、来源、向量、版本与确认/拒绝/删除事件。

记忆类型为 `USER_PREFERENCE`、`BUSINESS_TERM`、`VERIFIED_QUERY` 和 `TOOL_PATTERN`。候选记忆仅由明确的“记住”请求、可重复的成功纠错或用户主动保存产生；只有 `CONFIRMED` 状态参与召回。用户记忆严格按 `user_id` 隔离，全局业务记忆仅管理员可确认。

召回复用 BGE 的关键词、余弦相似度与 RRF，默认 Top-5。每个用户最多 500 条有效记忆，90 天未使用过期。清洗层拒绝 JWT、密码、手机号、邮箱、原始查询结果行和未脱敏工具输出。

## 接口与观测

所有管理接口必须验证 JWT 并根据当前用户查询：

- `GET /api/memories`
- `POST /api/memories/{id}/confirm`
- `POST /api/memories/{id}/reject`
- `DELETE /api/memories/{id}`
- `DELETE /api/conversations/{id}`
- `GET /api/runs/{id}`

Harness 评测输入为真实运行轨迹，报告完成率、平均工具调用数、纠错成功率、Memory Recall@5、错误记忆采用率、平均延迟和失败分类。缺少分母时输出 `null`/“无可用样本”，不视为 0% 或 100%。
