# Enterprise Database Copilot

Enterprise Database Copilot 是一个面向企业数据库的受控 Text2SQL Agent 框架。使用者下载项目后，可以接入自己的 OpenAI-compatible 大模型 API，或接入本地 Ollama 模型，在浏览器里体验一个带权限、审计、证据链和安全 SQL 防护的数据库 Copilot。

这个项目的重点不是“让模型直接写 SQL 然后执行”，而是把自然语言问题拆成可验证流程：语义目录检索、Schema/Value Linking、QueryPlan 校验、安全 SQL 执行、证据展示和审计追踪。业务写操作与只读查询彻底分离，写操作只能走固定业务接口和用户确认。

## 适合谁

- 想快速体验 Text2SQL Agent 的开发者。
- 想把自己的大模型 API 接到数据库问答系统里的团队。
- 想研究企业级 Agent 安全边界、审批、审计、评测和灰度发布的人。
- 想基于现有框架二次开发数据库 Copilot、BI Copilot 或运营分析 Agent 的同学。

## 功能概览

- 自然语言查询 PostgreSQL 示例业务库。
- 支持 OpenAI-compatible API，也支持本地 Ollama。
- 浏览器 UI：登录后直接对话体验 agent。
- 只读 SQL Guard：拒绝 DML、DDL、多语句、危险函数、敏感字段和超时查询。
- Schema/Value Linking：基于语义目录把问题链接到表、字段、值和 confirmed join。
- QueryPlan 校验：SQL 执行前核对计划中的表、字段、过滤、分组、排序和 join。
- Evidence API / Drawer：展示脱敏后的查询依据、计划、验证状态和来源 hash。
- Harness：统一 run_id、状态机、预算、工具调用上限、纠错和澄清续跑。
- Memory / Context Compiler：用户确认后才写入记忆，并在上下文编译时记录 provenance。
- 评测套件：包含回归题、Memory/Context、Grounding 和阶段 D 发布门槛。
- 灰度开关：Grounding、Context、Evidence、OTel、Clarification、Rollout 均支持 `off|shadow|enforce`。

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

主要目录：

- `agent-service`：Vanna Agent、安全 SQL、Schema/Value Linking、QueryPlan、Evidence、Harness。
- `business-service`：JWT、固定业务接口、审批状态机、事务、乐观锁和审计。
- `database/init`：PostgreSQL 示例 Schema、样例数据、语义目录、只读角色和迁移。
- `evaluation`：回归、Memory、Grounding、Context、Release 评测集。
- `docs`：Text2SQL 研究映射、架构说明、发布和回滚手册。
- `scripts`：本地启动、迁移、评测和开源发布检查脚本。

## 5 分钟快速体验

### 1. 克隆项目

```bash
git clone https://github.com/turnturnturnturn/Text2SQL-turnrevise.git
cd Text2SQL-turnrevise
```

如果 GitHub 默认分支不是 `main`，可以直接切到当前开源发布分支：

```bash
git checkout agent/framework-only-oss-release
```

### 2. 准备环境

推荐使用 Docker Desktop。建议给 Docker 分配 4-5 GB 内存。

本地需要：

- Docker Desktop
- 一个可用的大模型服务，二选一：
  - 云端或学校/公司提供的 OpenAI-compatible API
  - 本地 Ollama 模型

### 3. 创建配置文件

```bash
cp .env.example .env
```

打开 `.env`，至少修改这些字段：

```dotenv
JWT_SECRET=replace-with-at-least-32-random-characters
INTERNAL_SERVICE_TOKEN=replace-with-a-random-service-token
LLM_PROVIDER=openai
OPENAI_API_KEY=your-provider-api-key
OPENAI_MODEL=your-provider-model
OPENAI_BASE_URL=https://your-provider.example/v1
```

不要把真实 API Key 提交到 GitHub。`.env` 是本地私密配置文件，项目不会要求你把模型权重或 API Key 放进仓库。

### 4. 启动服务

```bash
./scripts/docker-compose.sh up --build -d
```

查看容器状态：

```bash
./scripts/docker-compose.sh ps
```

健康检查：

```bash
curl http://localhost:8000/health
curl http://localhost:8080/actuator/health
```

### 5. 打开浏览器体验

访问：

```text
http://localhost:8000/app
```

开发演示账号：

| 用户名 | 密码 | 角色 | 能力 |
|---|---|---|---|
| `analyst` | `analyst123` | analyst | 只读查询 |
| `operator` | `operator123` | operator | 只读查询 + 业务写预览 |
| `admin` | `admin123` | admin | 管理员演示能力 |

可以先用这些问题测试：

```text
最近 30 天销售额是多少？
各品类的订单金额排名前 5 是什么？
退款被拒绝的订单有多少？
找出最近 7 天付款但未发货的订单。
```

## 接入自己的大模型 API

本项目使用 OpenAI-compatible Chat Completions 接口。只要你的服务支持类似下面的请求，就可以接入：

```bash
curl -X POST "https://your-provider.example/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{"model":"your-provider-model","messages":[{"role":"user","content":"你好"}]}'
```

在 `.env` 中填写：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=your-provider-model
OPENAI_BASE_URL=https://your-provider.example/v1
```

常见例子：

```dotenv
# OpenAI 官方
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
OPENAI_BASE_URL=
```

```dotenv
# 第三方或学校统一入口
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=smart/reasoning
OPENAI_BASE_URL=https://api.example.edu.cn/v1
```

改完 `.env` 后重启 agent：

```bash
./scripts/docker-compose.sh restart agent-service
```

如果你的 API 需要校园网、公司内网或 VPN，请先连上 VPN，再启动或重启 `agent-service`。浏览器仍然打开本地地址 `http://localhost:8000/app`，不是打开模型 API 地址。

## 接入本地 Ollama

如果你想完全本地体验，可以先安装并启动 Ollama，然后拉取一个模型：

```bash
ollama pull llama3.2
ollama serve
```

`.env` 配置：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.2
```

然后启动或重启服务：

```bash
./scripts/docker-compose.sh up --build -d
```

说明：

- Docker 容器访问宿主机 Ollama 时通常使用 `http://host.docker.internal:11434`。
- 模型越小，速度越快，但 Text2SQL、工具调用和澄清能力会弱一些。
- 这个开源仓库不包含本地模型权重，也不绑定特定 Qwen、Llama 或其他模型。使用者可以自行选择 API 或本地模型。

## 灰度开关

默认配置偏保守，新能力大多以 `shadow` 运行：不改变主回答路径，但会记录计划、证据和差异。

```dotenv
GROUNDING_V2_MODE=shadow
CONTEXT_HARNESS_V2_MODE=shadow
EVIDENCE_DRAWER_MODE=shadow
OTEL_MODE=shadow
CLARIFICATION_RESUME_MODE=shadow
ROLLOUT_POLICY_MODE=shadow
```

模式含义：

- `off`：关闭新链路，走旧路径。
- `shadow`：运行新链路并记录差异，但不阻断当前答案。
- `enforce`：严格启用校验，缺少有效证据、计划或安全条件时 fail closed。

生产环境建议先从 `shadow` 开始，完成评测和审计后再逐步切到 `enforce`。

## 安全边界

- Vanna 原生 `RunSqlTool` 未注册。
- `SafeReadSqlTool` 只允许白名单 `SELECT` 和只读 CTE。
- SQL 使用独立 PostgreSQL 只读账号，启用只读事务、5 秒超时和 200 行限制。
- `analyst` 只能查询；`operator` 可以预览订单写操作；软删除仅限 `admin`。
- 写操作必须先生成审批记录，确认令牌绑定用户、操作内容、数据版本和有效期。
- 确认按钮发送确定性命令，由 WorkflowHandler 截获，不交给 LLM 决策。
- 审计不可用时默认拒绝执行。
- Restricted 资产、candidate join、过期值和未解决歧义不会进入自动执行证据。
- `enforce` 模式下，无有效 QueryPlan 不执行 SQL。

## 支持的业务写操作

示例业务服务只开放固定动作，没有任意 SQL 写接口。

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

订单状态机：

```text
DRAFT -> UNPAID -> PAID -> SHIPPED
  |         |
  +------> CANCELLED
```

## 运行评测

Python 单元测试：

```bash
cd agent-service
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
```

Java 单元测试：

```bash
cd business-service
mvn test
```

Grounding v2 离线验收：

```bash
./scripts/evaluate_grounding.py --check
```

完整本地验证：

```bash
./scripts/verify-all.sh
```

真实模型端到端评测需要显式打开：

```bash
RUN_E2E=1 ./scripts/verify-all.sh
```

离线评测和 live-model 指标严格分开；如果本地模型或 API 不可用，项目不会据此宣称 Text2SQL 提升。

## 常见问题

### 浏览器应该打开哪个地址？

打开本地 UI：

```text
http://localhost:8000/app
```

模型 API 地址只给后端调用。即使你的模型入口需要 VPN，连上 VPN 后也还是打开本地 UI。

### API Key 测试成功，但页面回答失败怎么办？

先重启 agent：

```bash
./scripts/docker-compose.sh restart agent-service
```

再看日志：

```bash
./scripts/docker-compose.sh logs -f agent-service
```

常见原因是 `OPENAI_BASE_URL` 少了 `/v1`，模型名写错，或 VPN/网络代理只对浏览器生效、没有对 Docker/终端生效。

### 本地模型太小会影响效果吗？

会。小模型通常能跑通流程，但复杂 Text2SQL、工具选择、澄清和多跳推理会明显弱一些。建议先用 OpenAI-compatible 云端模型验证框架，再用本地模型做成本、隐私和延迟优化。

### 可以换自己的数据库吗？

可以，但需要补齐三件事：

- 修改 `database/init` 中的业务表和样例数据。
- 维护 `semantic_catalog`、指标口径、字段描述、状态值字典和 confirmed join。
- 增加对应的评测 case，确保安全 SQL、QueryPlan 和 Evidence 仍然可验证。

## 上线前必须调整

- 替换全部开发密码和 HS256 密钥，推荐接入正式身份系统或非对称 JWT。
- 将只读角色创建移出初始化 SQL，交给密钥管理和数据库运维系统维护。
- 根据数据规模评估 pgvector 或外部向量数据库。
- 在反向代理上配置 TLS、限流、SSE 超时和安全响应头。
- 将演示登录壳替换为正式身份提供方。
- 明确 trace、metric、audit 和 memory 的保留周期与脱敏策略。

## 许可证

本项目使用 MIT License。详见 [LICENSE](LICENSE)。
