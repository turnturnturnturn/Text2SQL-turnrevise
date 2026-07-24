# External API-only LLM 迁移

状态：已完成

## 目标

删除本地生成式模型支持，只保留 OpenAI-compatible 外部 API，同时保持数据库安全边界和现有 Agent 功能。

## 安全约束

- 不提交或输出 `.env`、API Key、JWT、数据库密码、PII 或结果行
- 不删除无关模型或其他项目缓存
- 不改变 SQL Guard、审批、身份认证和数据库权限
- 不将 Oracle 指标描述为真实模型指标

## 验收命令

```bash
./scripts/verify-all.sh
docker compose config --quiet
git diff --check
```

## 实际结果

- Python：111 passed
- Memory：30 cases，Recall@5 1.0，不合格记忆暴露率 0.0
- Grounding：Table Recall@5 0.9796、Column Recall@10 0.9574、Join 1.0、Value 1.0
- Java：Docker Maven 构建成功
- Compose：`docker compose config --quiet` 通过
- 本地文件：已删除项目运行目录、两个生成模型缓存目录、转换运行环境和本地推理 blobs
