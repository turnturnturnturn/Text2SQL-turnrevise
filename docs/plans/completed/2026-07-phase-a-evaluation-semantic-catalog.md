# 任务：vNext 阶段 A 评测与语义资产底座

## 目标与验收

- 用户价值：把“记忆是否找对、是否泄露错误/跨用户内容”变成可重复运行的专项指标，并为后续 Schema/Value Linking 提供有版本、有来源、有敏感等级的统一语义目录。
- 成功标准：
  - 新增不少于 30 条带 gold memory id 的专项题集；
  - 评测输出 Memory Recall@5、错误记忆暴露率和不合格记忆暴露率；
  - candidate、rejected、expired、跨用户记忆不参与召回；
  - 新增 `semantic_catalog` 幂等迁移，将现有 Schema、指标和验证 SQL 映射为统一资产；
  - Agent 运行账号对语义目录只有 `SELECT` 权限；
  - Python 与 Java 回归测试通过。
- 验收命令：
  - `cd agent-service && .venv/bin/python -m pytest`
  - `./scripts/verify-all.sh`
  - `./scripts/apply-knowledge-migration.sh` 连续执行两次（有本地 Docker/PostgreSQL 时）

## 边界

- 允许修改：`agent-service/app/evaluation.py`、记忆评测辅助模块、对应测试、`evaluation/`、`scripts/evaluate_memory.py`、`database/migrations/003_semantic_catalog.sql`、迁移入口与阶段 A 文档。
- 主 Agent 授权修改：数据库迁移与迁移入口；本轮不修改 `main.py`、`config.py`、`compose.yaml` 和依赖文件。
- 本轮不实现：QueryPlan、双向 Schema Linking、Value Linking 在线链路、Context Compiler v2、Harness v2 状态扩展、前端证据抽屉。
- 不得回退的安全不变量：
  - 查询仍只允许单条 `SELECT`/只读 CTE、5 秒、200 行；
  - 业务写操作仍只由 business-service 固定 API 与人工确认执行；
  - candidate/rejected/expired/跨用户记忆不得进入 Prompt；
  - 不把 PII、密钥或原始结果行写入记忆题集或语义目录；
  - 评测缺少真实 adopted memory ids 时，错误记忆“采用率”继续为 `null`，不得用召回/暴露冒充采用。

## 实现契约

- 记忆题集：稳定 fixture id，区分 gold、incorrect 与 ineligible memory ids；运行时使用实际 `MemoryService.search_confirmed`，不预写结果。
- 记忆指标：
  - Recall@5：Top-5 是否命中至少一个 gold id；
  - 错误记忆暴露率：声明 incorrect ids 的样本中，Top-5 命中错误 id 的比例；
  - 不合格记忆暴露率：candidate/rejected/expired/跨用户 id 进入 Top-5 的比例；
  - 错误记忆采用率：仅当轨迹提供 `adopted_memory_ids` 时计算。
- 语义目录：`semantic_assets`、`schema_relations`、`value_dictionary`；每个资产带 tenant、版本、来源 hash、trust、sensitivity、effective time 与 status。
- 迁移兼容：重复执行不产生重复资产；现有 `schema_catalog`、`metric_definitions`、`query_examples` 保留，当前检索链不切换。
- 失败与恢复：题集结构非法时 fail fast；无样本指标输出 `null`；迁移使用事务与 `ON CONFLICT` 幂等更新。

## 交付回执

- 分支：`main`
- Commit SHA：见本次交付响应中的最终 SHA。
- 修改摘要：新增 30 条 gold-memory 专项题集与真实 `MemoryService` 评测；拆分召回、错误暴露、不合格暴露和采用率；新增版本化语义资产模型、数据库目录迁移、权限收敛、迁移入口及架构文档。
- 测试结果：`./scripts/verify-all.sh` 通过（Python 83 passed；Memory 30 cases，Recall@5=100%、不合格暴露率=0%；Java 9 passed）。`003_semantic_catalog.sql` 连续执行后对象/版本快照均保持 `39|4|8|39|4|8`；`copilot_readonly` 可读不可写，`copilot_agent_state` 无目录访问权且不能读取 `public.orders`。
- 遗留风险：错误记忆暴露率基线为 100%，说明当前已确认但内容错误的记忆仍会进入 Top-5；本阶段只建立可观测基线，下一阶段需通过来源可信度、版本冲突、时间有效性和上下文预算策略降低该指标。在线检索仍使用旧表，尚未切换到新语义目录。完整 E2E 需要本地模型与服务凭据，本轮未运行。
