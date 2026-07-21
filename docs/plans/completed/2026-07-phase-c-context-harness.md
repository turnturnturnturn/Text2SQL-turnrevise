# 任务：vNext 阶段 C Context Compiler v2 与 Harness v2

## 目标与验收

- 新增可追溯 `ContextManifest`、分区 token 预算、不可裁剪安全分区和 progressive disclosure 条目。
- 新增版本化 `ConversationState`，原始消息不变，压缩失败回退最近 6 轮。
- 分离“用户确认”与“事实可用”，错误、冲突、过期、被取代记忆不进入上下文。
- 新增不确定性门控、单问题澄清卡、用户所属校验和一次性 child run。
- 新增阶段状态、checkpoint、取消与只读恢复边界；写操作零恢复、零重放。
- `CONTEXT_HARNESS_V2_MODE=off|shadow|enforce`，默认 `shadow`，现有 SSE/JWT/审批/只读 SQL 接口兼容。

## 安全不变量

- 只允许单条只读 SQL、5 秒、200 行；阶段 C 未改变 SQL Guard 边界。
- 安全、身份、角色、tenant 和工具权限永远高于会话、摘要、记忆和检索内容。
- 澄清选择不自动写入长期记忆。
- checkpoint、manifest 和 conversation state 不保存原始结果行、PII、密钥或 JWT。
- 只读恢复创建新 child run；原 run 保持 `FAILED/process_restarted`。

## 交付回执

- 分支：`feat/phase-c-context-harness`
- 实现：Context Compiler v2、脱敏 manifest/checkpoint、结构化会话状态、记忆 validity/conflict/supersede、Harness v2 阶段与安全恢复、Grounding 澄清卡、一次性 child run、取消与三态灰度。
- Python：`137 passed`；Java：`9 passed, 0 failed`。
- Context 专项：provenance `100%`，关键约束召回 `100%`，必要澄清识别 `100%`，非必要澄清 `0%`，重启闭合 `100%`，写恢复 `0`。
- Memory：30 题 Recall@5 `100%`，错误记忆暴露 `0%`，不合格记忆暴露 `0%`。
- Grounding 回归：Table Recall@5 `97.96%`，Column Recall@10 `95.74%`，Join Exact `100%`，Value Recall@3 `100%`，candidate Join 自动执行 `0`。
- 迁移：`005_context_harness_v2.sql` 在新 PostgreSQL 16 容器初始化后连续重复执行两次成功；5 张阶段 C 表、4 个关键约束稳定，`copilot_agent_state` 具有所需权限，`copilot_readonly` 无访问权；退款状态仅 `REJECTED/PUBLISHED`。

## 遗留边界

- 未运行本地模型 live-agent E2E；本次不宣称新的模型 Text2SQL 指标。
- 阶段 C 的恢复 child 只创建可审计新 run，不在 `POST /clarify` 请求内嵌套启动新 SSE 模型流；客户端需继续发起 child 交互。
- QueryPlan v1 对 CTE、集合运算和嵌套多作用域的 enforce 限制仍保留。

