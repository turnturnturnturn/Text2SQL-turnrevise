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
