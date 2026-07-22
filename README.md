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
- `RequestHarness` 为每次请求提供统一 `run_id`、状态机、120 秒总预算、8 次工具上限和 2 次只读纠错上限。
- 会话、运行轨迹和长期记忆写入独立 `agent_state` Schema；该账号没有业务表读写权限。
- “记住/以后按……”只生成候选卡片，用户确认后才会作为不可信参考上下文参与召回。
- Grounding v2 从版本化目录执行双向 Schema Linking、confirmed Join 寻路和受控 Value Linking；Restricted 与候选关系不进入自动执行证据。
- `enforce` 模式要求 SQL 前存在有效 QueryPlan，并确定性核对表、字段、值、分组和 Join；默认 `shadow` 只记录差异。
- Context Compiler v2 为安全、请求、计划、Grounding、证据、记忆与会话分配 token 预算，并为所有入模条目记录来源；安全与当前请求不可裁剪。
- 结构化 ConversationState 保留约束、选择、拒绝项和来源 message id；错误、冲突、过期及被取代记忆不会进入模型上下文。
- Harness v2 支持阶段状态、脱敏 checkpoint、取消和只读恢复 child run；业务写入与审批永不自动恢复或重放。
- `enforce` 下 Grounding 的来源化歧义会停在 `NEEDS_CLARIFICATION`，用户回答一次性澄清卡后创建新的只读 child run。

## 目录

- `agent-service`：Vanna Agent、安全 SQL、Schema/指标检索、审批卡片。
- `business-service`：JWT、固定 CRUD、审批状态机、事务、乐观锁、审计。
- `database/init`：电商样例 Schema、数据、指标口径与只读角色。
- `compose.yaml`：PostgreSQL 和两个服务的一键编排。
- `evaluation`：60 条中文标准题集与自动化质量报告。
- `evaluation/memory_cases.json`：30 条 gold memory、错误记忆和隔离场景专项题集。
- `evaluation/grounding_cases.json`：25 条 Schema/Join 与 20 条受控值链接专项题集。
- `evaluation/context_cases.json`：15 条长上下文关键约束、10 条澄清和 3 条恢复安全专项题集。
- `docs/TEXT2SQL_RESEARCH_NOTES.md`：最新 Text2SQL 研究映射、已落地优化及后续路线。
- `docs/architecture/HARNESS_AND_MEMORY.md`：Harness、上下文编译、持久记忆和管理接口。
- `AGENTS.md`：Codex worktree 协作、安全不变量与统一完成标准。

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

已有数据卷升级时先运行幂等迁移：

```bash
./scripts/apply-knowledge-migration.sh
```

该命令会创建只读 `semantic_catalog`，把现有 Schema、指标和验证 SQL 映射为带版本、来源哈希、信任级别和敏感度的统一语义资产，导入人工维护的低基数状态值，并创建脱敏 QueryPlan 记录。`GROUNDING_V2_MODE=off|shadow|enforce` 控制灰度；默认 `shadow` 保持当前答案路径，只增加证据和计划差异观测。

阶段 C 的状态表和上下文清单可单独幂等升级：

```bash
./scripts/apply-context-migration.sh
```

`CONTEXT_HARNESS_V2_MODE=off|shadow|enforce` 默认同样为 `shadow`：`off` 使用旧上下文和旧生命周期；`shadow` 持久化脱敏 manifest/ConversationState 但不改变当前回答；`enforce` 使用分区编译、阶段状态和澄清门控。回滚只需切回 `off`，无需删除阶段 C 数据。

切换策略：

- `off`：只运行旧 hybrid retrieval；
- `shadow`：旧结果继续供模型使用，同时运行 v2 并记录 evidence；
- `enforce`：模型使用 v2 目录，必须依次完成 `search_schema_knowledge`、`validate_query_plan`、`safe_read_sql`。

回滚只需改回 `off`，无需删除新表或迁移数据。

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

### OpenAI-compatible API

使用任意兼容 OpenAI API 的云端或自托管服务。项目不附带模型、模型转换工具或本机模型运行环境：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-provider-api-key
OPENAI_MODEL=your-provider-model
# 第三方或自托管兼容服务设置 OPENAI_BASE_URL
OPENAI_BASE_URL=https://your-provider.example/v1
```

Ollama：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.2
```

启动容器：

```bash
./scripts/docker-compose.sh up --build -d
./scripts/docker-compose.sh ps
```

`docker-compose.sh` 会优先使用系统 PATH 中的 Docker；若 CLI 尚未建立全局链接，则自动使用 Docker Desktop 应用内置的 CLI 和凭据助手。

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

Grounding v2 离线验收不依赖数据库或模型下载：

```bash
./scripts/evaluate_grounding.py --check
```

该脚本调用生产 linker 代码，检查 Table Recall@5、Column Recall@10、Join Path Exact Match、Value Recall@3 和 candidate Join 零执行；这些指标不代表模型 Text2SQL 准确率。

脚本比较关键词基线与 BGE 中文向量 + RRF 混合检索，并将本次实际运行的 Recall@3、Recall@5、MRR、静态安全拦截率、Oracle SQL 执行成功率和完整结果等价率输出到 `evaluation/reports/`。增加 `--live-agent` 后，还会通过真实 SSE 接口评测已配置模型的 SQL 执行、结果等价、危险请求无执行和审计哈希关联率；`--harness-input` 可追加运行完成率、纠错率、Memory Recall@5、延迟和失败分类。详情参见 [评测说明](evaluation/README.md) 和 [作品集说明](PORTFOLIO.md)。

Agent Docker 镜像不包含 CUDA/NVIDIA 运行库；本地模型由使用者自行安装和运行。

端到端重点验证：

1. 多语句、DML、DDL、跨 Schema、敏感字段与超量 `LIMIT` 均被拒绝。
2. analyst 无法获得写工具，也不能调用业务写接口。
3. 过期、重复、篡改、跨用户或版本过期的审批无法执行。
4. 写操作失败时业务事务回滚，并产生失败审计记录。
5. `SELECT INTO`、`FOR UPDATE` 与未审核的 PostgreSQL/自定义函数均被拒绝。

## 上线前必须调整

- 替换全部开发密码和 HS256 密钥，推荐改为非对称 JWT。
- 将只读角色创建移出初始化 SQL，交由密钥管理和数据库运维系统维护。
- 根据数据规模评估 pgvector；当前小规模记忆使用 PostgreSQL `REAL[]` 与 Agent 内 RRF。
- 在反向代理上配置 TLS、限流、SSE 超时和安全响应头。
- 将演示登录壳替换为正式身份提供方，并自托管或锁定前端组件资源。
