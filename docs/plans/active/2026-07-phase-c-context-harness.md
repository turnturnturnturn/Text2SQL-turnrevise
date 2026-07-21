# 任务：vNext 阶段 C Context Compiler v2 与 Harness v2

## 目标与验收

- 新增可追溯 `ContextManifest`、分区 token 预算、不可裁剪安全分区和 progressive disclosure 条目。
- 新增版本化 `ConversationState`，原始消息不变，压缩失败回退最近 6 轮。
- 分离“用户确认”与“事实可用”，错误、冲突、过期、被取代记忆不进入上下文。
- 新增不确定性门控、单问题澄清卡、用户所属校验和一次性 child run。
- 新增阶段状态、checkpoint、取消与只读恢复边界；写操作零恢复、零重放。
- `CONTEXT_HARNESS_V2_MODE=off|shadow|enforce`，默认 `shadow`，现有 SSE/JWT/审批/只读 SQL 接口兼容。

## 修改边界

- 允许修改 `agent-service/app/context_v2`、`harness`、`state`、状态 API、配置、迁移、评测、文档和对应测试。
- 不改业务写 API 语义，不让 Agent 直接写业务表。
- 不实现前端证据抽屉、语义资产管理写接口和外部 trace 导出。

## 安全不变量

- 只允许单条只读 SQL、5 秒、200 行；阶段 C 不改变 SQL Guard 边界。
- 安全、身份、角色、tenant 和工具权限永远高于会话、摘要、记忆和检索内容。
- 澄清选择不自动写入长期记忆。
- checkpoint、manifest 和 conversation state 不保存原始结果行、PII、密钥或 JWT。
- 只读恢复必须创建新 child run；原 run 保持 `FAILED/PROCESS_RESTART`。

## 验收命令

- `cd agent-service && python -m pytest`
- `./scripts/evaluate_context.py --check`
- `./scripts/evaluate_memory.py --check`
- `./scripts/verify-all.sh`
- `./scripts/apply-context-migration.sh` 连续执行两次并检查对象、版本和权限稳定

