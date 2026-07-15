# Text2SQL 标准评测集

`questions.json` 固定包含 60 条中文题目：40 条字段、口径、时间、多表关联和 Top N 的知识检索题，10 条可执行的只读 SQL oracle 题，以及 10 条危险请求拦截题。每条题目声明关联知识条目；执行题声明预期结果行数；安全题声明必须拒绝的 SQL。

运行评测前，需要启动 PostgreSQL（或完整 Docker Compose）。使用只读连接执行：

```bash
cd agent-service
.venv/bin/python ../scripts/evaluate_retrieval.py \
  --database-url 'postgresql://copilot_readonly:copilot_readonly_dev@localhost:5432/enterprise_copilot'
```

如果 PostgreSQL volume 在本次改造前已经创建，先执行一次无损知识库迁移（不会修改订单、客户等业务数据）：

```bash
./scripts/apply-knowledge-migration.sh
```

脚本分别运行 `keyword` 和 `hybrid`，报告 Recall@3、Recall@5、MRR、Oracle SQL 执行成功率、完整结果等价率与静态安全拦截率。完整结果比较忽略行列顺序，但保留重复行差异，并以 `1e-6` 容差比较数值。模型首次下载会进入 `HF_HOME`；在 Docker Compose 中该目录由 `embedding-cache` volume 持久化。

启动 MLX 和三个 Docker 服务后，可增加真实 Agent 评测。它使用 analyst 登录，顺序执行 10 条查询和 10 条危险请求，从 SSE 安全查询组件读取实际结果，并用指令哈希核对审计事件：

```bash
EVAL_PASSWORD=analyst123 \
EVAL_AUDIT_DATABASE_URL='postgresql://管理员:密码@localhost:5432/enterprise_copilot' \
.venv/bin/python ../scripts/evaluate_retrieval.py \
  --database-url 'postgresql://copilot_readonly:copilot_readonly_dev@localhost:5432/enterprise_copilot' \
  --live-agent
```

报告会把 Retrieval、Oracle SQL 和 Qwen3-4B 实际 Agent 指标分开展示，不能用 Oracle 指标代替模型生成 SQL 的准确率。

报告写入 `evaluation/reports/`，该目录被 Git 忽略，避免把一次运行的指标误当作固定结论提交。

## Grounding v2 专项评测

`grounding_cases.json` 固定包含 25 条 Schema/Join Linking、20 条 Value Linking 正例和 3 条 Value 负例。冻结目录额外加入非目标表，避免“目录刚好只有五张表”导致 Recall@5 虚高；默认运行生产 `BidirectionalGroundingLinker` 的关键词降级路径，向量/RRF 路径由单元测试覆盖：

```bash
./scripts/evaluate_grounding.py --check
```

门槛为 Table Recall@5 ≥95%、Column Recall@10 ≥90%、Join Path Exact Match ≥85%、Value Recall@3 ≥90%，candidate Join 自动执行数与无关问题 Value 误召回数都必须为 0。传入 `--database-url` 可用实际 `semantic_catalog` 重跑相同题集；报告中的链接指标与模型 Text2SQL 指标严格分开。

## Memory 专项评测集

`memory_cases.json` 固定包含 30 条带 gold memory id 的中文题目，并在同一题集中放入：

- 正常的用户记忆与管理员确认的 GLOBAL 业务术语；
- 内容相近但业务含义错误的 confirmed 记忆，用于测量错误记忆暴露率；
- candidate、rejected、expired 和其他用户记忆，用于测量不合格记忆暴露率。

专项脚本直接调用生产 `MemoryService.search_confirmed` 路径，不预写召回结果，也不依赖数据库或模型下载：

```bash
.venv/bin/python ../scripts/evaluate_memory.py --check
```

去掉 `--check` 会在 `evaluation/reports/` 生成逐题 JSON 和 Markdown 报告。当前指标定义：

- Memory Recall@5：Top-5 是否命中至少一个 gold memory id；
- 错误记忆暴露率：带 incorrect memory ids 的样本中，Top-5 命中错误 id 的比例；
- 不合格记忆暴露率：candidate、rejected、expired 或跨用户 id 进入 Top-5 的比例；
- 错误记忆采用率：只有真实 Agent 轨迹提供 `adopted_memory_ids` 时才计算，不能用“被召回”冒充“被采用”。

`./scripts/verify-all.sh` 会自动运行该专项题集，并检查 Recall@5 与不合格记忆隔离门槛。

## Harness 指标

如需在同一 JSON/Markdown 报告中聚合 Harness 与记忆指标，传入真实运行轨迹：

```bash
.venv/bin/python ../scripts/evaluate_retrieval.py \
  --database-url "$DATABASE_URL" \
  --harness-input ../evaluation/harness-input.json
```

`harness-input.json` 的最小结构为：

```json
{
  "runs": [
    {
      "status": "COMPLETED",
      "tool_call_count": 2,
      "correction_attempted": false,
      "latency_ms": 830
    }
  ],
  "memory_cases": [
    {
      "id": "memory-001",
      "gold_memory_ids": ["expected-id"],
      "retrieved_memory_ids": ["expected-id"],
      "incorrect_memory_ids": ["known-wrong-id"],
      "ineligible_memory_ids": ["candidate-or-cross-user-id"],
      "adopted_memory_ids": []
    }
  ]
}
```

纠错成功率只以 `correction_attempted=true` 的运行为分母；Memory Recall@5 只以声明 `gold_memory_ids` 的样本为分母；错误记忆采用率只以同时提供错误 id 和真实 adopted id 的轨迹为分母。没有可用样本时指标为 `null`，不预设为 0% 或 100%。旧版 `incorrect_memory_present` / `incorrect_memory_adopted` 字段仍可读取，但新报告应优先使用可追溯 id。
