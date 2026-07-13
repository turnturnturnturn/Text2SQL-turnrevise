# Enterprise Database Copilot

一个基于 Vanna 2.0.2、FastAPI、Spring Boot 与 PostgreSQL 的受控数据库 Agent。它把自然语言查询与业务写操作彻底分开：查询只能通过只读 SQL 工具，写操作只能调用固定业务接口并经过用户确认。

## 架构

```text
Browser / <vanna-chat>
        |
        | JWT + SSE
        v
agent-service (FastAPI + Vanna)
  |                     |
  | read-only SQL       | typed action preview
  v                     v
PostgreSQL          business-service (Spring Boot)
                          |
                          | transaction + optimistic lock + audit
                          v
                     PostgreSQL
```

安全边界：

- Vanna 原生 `RunSqlTool` 未注册；`SafeReadSqlTool` 仅接受白名单 `SELECT`/只读 CTE。
- 查询使用独立 PostgreSQL 只读账号，并启用只读事务和 5 秒超时。
- `analyst` 只能查询；`operator` 可预览订单写操作；软删除仅限 `admin`。
- 每次写操作先生成待审批记录，确认令牌绑定用户、操作内容、数据版本和 5 分钟有效期。
- 确认按钮发送确定性命令，由 WorkflowHandler 截获，不交给 LLM 决策。
- Vanna 工具访问、SQL 调用、结果、耗时及原始指令哈希统一写入 `audit_events`；默认审计不可用时拒绝执行。

## 目录

- `agent-service`：Vanna Agent、安全 SQL、Schema/指标检索、审批卡片。
- `business-service`：JWT、固定 CRUD、审批状态机、事务、乐观锁、审计。
- `database/init`：电商样例 Schema、数据、指标口径与只读角色。
- `compose.yaml`：PostgreSQL 和两个服务的一键编排。
- `evaluation`：60 条中文标准题集与自动化质量报告。
- `docs/TEXT2SQL_RESEARCH_NOTES.md`：最新 Text2SQL 研究映射、已落地优化及后续路线。

## 本地要求

- Docker Desktop（推荐 4–5 GB 内存配额）
- 或者 Python 3.11、JDK 21、Maven 3.6.3+、PostgreSQL 16
- 云端模式需要 OpenAI-compatible API key；本地模式需要 Ollama

你当前下载的 `/Users/turn/Downloads/vanna-main` 保留为源码参考，本工程不会修改它。生产构建固定依赖 `vanna==2.0.2`。

## 启动

```bash
cp .env.example .env
# 编辑 .env，至少设置 JWT_SECRET、INTERNAL_SERVICE_TOKEN 和 OPENAI_API_KEY
docker compose up --build
```

服务地址：

- 带 JWT 登录的 Copilot UI：<http://localhost:8000/app>
- Vanna 原始调试页：<http://localhost:8000>
- Spring Boot：<http://localhost:8080>
- 健康检查：<http://localhost:8000/health>、<http://localhost:8080/actuator/health>

开发样例账号仅用于本机：

| 用户名 | 密码 | 角色 |
|---|---|---|
| analyst | analyst123 | analyst |
| operator | operator123 | operator |
| admin | admin123 | admin |

登录：

```bash
curl -s http://localhost:8080/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"operator123"}'
```

浏览器调用 `/api/vanna/v2/chat_sse` 时必须携带返回的 `Authorization: Bearer <token>`。`/app` 登录壳页面会把 Token 保存在当前标签页的 `sessionStorage`，并通过 Web Component 的 `setCustomHeaders` 注入请求；关闭标签页后需要重新登录。

## 模型切换

### 本机魔塔 Qwen3-4B（Apple Silicon 推荐）

已有魔塔 BF16 权重时，可转换为 MLX 4-bit 模型并提供 OpenAI-compatible API：

```bash
./scripts/prepare-local-qwen.sh
./scripts/start-local-qwen.sh
```

第二条命令需保持运行。它使用 Apple Metal 在 `0.0.0.0:8081` 启动仅供开发使用、无认证的模型服务；不要在不可信网络中长期开放。

`.env` 配置为：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=local-mlx
OPENAI_MODEL=default_model
OPENAI_BASE_URL=http://host.docker.internal:8081/v1
```

这里选择 `openai` 是因为 MLX 暴露的是 OpenAI-compatible 协议，并不产生 OpenAI API 费用。启动容器：

```bash
./scripts/docker-compose.sh up --build -d
./scripts/docker-compose.sh ps
```

`docker-compose.sh` 会优先使用系统 PATH 中的 Docker；若 CLI 尚未建立全局链接，则自动使用 Docker Desktop 应用内置的 CLI 和凭据助手。

云端 OpenAI-compatible API：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5
# 第三方兼容服务可设置 OPENAI_BASE_URL
```

Ollama：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:4b
```

## 支持的写操作

- `CREATE_DRAFT_ORDER`
  - `payload.customerId`
  - `payload.items = [{"productId": 1, "quantity": 2}]`
- `UPDATE_ORDER_STATUS`
  - `payload.orderId`
  - `payload.targetStatus`
- `CANCEL_ORDER`
  - `payload.orderId`
- `SOFT_DELETE_DRAFT`
  - `payload.orderId`
  - 仅 `admin`

订单状态机为：

```text
DRAFT -> UNPAID -> PAID -> SHIPPED
  |         |
  +------> CANCELLED
```

所有写操作都必须先调用 `/internal/actions/preview`，随后使用一次性令牌确认或取消。系统没有任意 SQL 写接口。

## 测试

Python：

```bash
cd agent-service
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

Java：

```bash
cd business-service
mvn test
```

查询质量评测（Docker 启动后执行）：

```bash
cd agent-service
.venv/bin/python ../scripts/evaluate_retrieval.py \
  --database-url 'postgresql://copilot_readonly:copilot_readonly_dev@localhost:5432/enterprise_copilot'
```

脚本比较关键词基线与 BGE 中文向量 + RRF 混合检索，并将本次实际运行的 Recall@3、Recall@5、MRR、静态安全拦截率、Oracle SQL 执行成功率和完整结果等价率输出到 `evaluation/reports/`。增加 `--live-agent` 后，还会通过真实 SSE 接口分别评测本地 Qwen 的 SQL 执行、结果等价、危险请求无执行和审计哈希关联率。已有数据库 volume 先执行 `./scripts/apply-knowledge-migration.sh`；该迁移仅补充知识卡片。详情参见 [评测说明](evaluation/README.md) 和 [作品集说明](PORTFOLIO.md)。

Agent Docker 镜像显式安装 PyTorch ARM64 CPU wheel，不包含 CUDA/NVIDIA 运行库；Apple Metal 仅由宿主机 MLX 使用。

端到端重点验证：

1. 多语句、DML、DDL、跨 Schema、敏感字段与超量 `LIMIT` 均被拒绝。
2. analyst 无法获得写工具，也不能调用业务写接口。
3. 过期、重复、篡改、跨用户或版本过期的审批无法执行。
4. 写操作失败时业务事务回滚，并产生失败审计记录。
5. `SELECT INTO`、`FOR UPDATE` 与未审核的 PostgreSQL/自定义函数均被拒绝。

## 上线前必须调整

- 替换全部开发密码和 HS256 密钥，推荐改为非对称 JWT。
- 将只读角色创建移出初始化 SQL，交由密钥管理和数据库运维系统维护。
- 将内存会话/Agent Memory 替换为 PostgreSQL/pgvector 持久化。
- 在反向代理上配置 TLS、限流、SSE 超时和安全响应头。
- 将演示登录壳替换为正式身份提供方，并自托管或锁定前端组件资源。
