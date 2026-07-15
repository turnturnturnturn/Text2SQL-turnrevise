# 任务：vNext 阶段 B Schema/Value Linking 与 QueryPlan

## 目标与验收

- 基于 `semantic_catalog` 实现表→列、列→表双向链接、最长 5 跳 confirmed Join 路径和受控值 Top-3 链接。
- 新增严格 QueryPlan、请求级证据链和 SQL 对齐校验；`GROUNDING_V2_MODE` 支持 `off|shadow|enforce`，默认 `shadow`。
- 新增脱敏 run evidence 查询；保持 SSE、JWT、审批与现有工具参数兼容。
- 专项门槛：Table Recall@5 ≥95%、Column Recall@10 ≥90%、Join Path Exact Match ≥85%、Value Recall@3 ≥90%、candidate Join 自动执行为 0。
- 回归：现有 60 题、Memory 30 题、全部 Python/Java 测试不回退。

## 修改边界

- 允许修改 `agent-service/app/grounding`、检索/工具/Guard/状态 API 与对应测试、数据库迁移、评测脚本题集、配置、Prompt、README 和架构文档。
- 主 Agent 负责高冲突入口 `main.py`、`config.py`、数据库迁移与统一验收脚本。
- 不实现 Context Compiler v2、Harness checkpoint/child run、前端证据抽屉或语义资产管理写 API。

## 安全不变量

- 只允许单条只读 SQL、5 秒、200 行；写操作仍只由 business-service 审批执行。
- 自动链路排除 Restricted、未发布、过期和 candidate/rejected Join；不得扫描业务表建立值索引。
- QueryPlan 与 evidence 不保存 PII、原始结果行、密钥或原始用户指令。
- `enforce` 无有效计划时 fail closed；`shadow` 只观测、不改变当前执行结果；`off` 保留旧路径。

## 验收命令

- `cd agent-service && .venv/bin/python -m pytest`
- `./scripts/evaluate_grounding.py --check`
- `./scripts/evaluate_memory.py --check`
- `./scripts/verify-all.sh`
- `./scripts/apply-knowledge-migration.sh` 连续执行两次并检查权限/版本稳定

## 交付回执

- 分支：`main`
- 实现 Commit SHA：提交后回填
- 修改摘要：完成目录端点门控、关键词/向量 RRF 双向链接、受控 Value Top-3、confirmed-only 最长 5 跳 BFS、严格 QueryPlan/证据持久化、三态发布、SQL 对齐 Guard 和 run evidence API；修正退款状态 `FAILED`→`REJECTED`。
- 测试结果：统一验收通过；Python 108/108、Java 9/9。Grounding：Table Recall@5 97.96%、Column Recall@10 95.74%、Join Exact Match 100%、Value Recall@3 100%、candidate Join 自动执行 0、Value 负例误召回 0。Memory：30 题 Recall@5 100%、ineligible 暴露 0。
- 遗留风险：最终版 `004` 未获批进行第二次实库执行与对象快照复核；完整 live-agent E2E 因未启用本地模型而跳过。已确认但内容错误的记忆暴露率仍为 100%，按范围留到阶段 C。当前按 PRD 假设固定 `tenant=default`，尚未实现多租户/角色级语义目录隔离。QueryPlan v1 在 `enforce` 下对 CTE、集合运算和嵌套多作用域 SQL fail closed。
