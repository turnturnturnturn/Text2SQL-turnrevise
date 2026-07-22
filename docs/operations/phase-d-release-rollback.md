# 阶段 D 发布与回滚手册

## 发布门槛

1. 连续执行迁移两次，确认对象数、版本、权限和退款状态字典不漂移。
2. 运行 `PYTHON=/path/to/python ./scripts/verify-all.sh`；live agent 仅在配置本地模型与数据库后以 `RUN_E2E=1` 开启。
3. 运行 `scripts/evaluate_release.py --check`，确认 160 个唯一 case、危险执行为 0、60 题严格等价率 100%。无模型时 `live_model` 必须为 `null`。
4. 检查敏感遥测、跨用户访问、业务写恢复均为 0，provenance 与终态闭合率均为 100%。

浏览器与 business-service 分端口部署时，必须把实际 Agent 页面 origin 写入 `BUSINESS_ALLOWED_ORIGINS`（逗号分隔、包含 scheme 与端口），否则浏览器登录预检会被拒绝。`AGENT_ALLOWED_ORIGINS` 继续控制 agent-service 自身的跨域来源，两者不可混用。

## 灰度顺序

| 阶段 | cohort | 最短观察 | 晋级条件 |
|---|---:|---:|---|
| Internal | 内部账号 | 24 小时 | 零安全事件、错误率不劣化 |
| Canary | 10% | 48 小时 | 门槛全部满足、P95 合格 |
| Broad | 50% | 72 小时 | 无自动降级、澄清续跑达标 |
| GA | 100% | 持续 | 保留自动监测与回滚能力 |

每次扩量创建新 policy version，禁止修改历史决策。Evidence、OTel、resume、Grounding 和 Context Harness 的有效模式必须作为同一次请求级决策保存。

## 回滚

运行 `scripts/drill-phase-d-rollback.sh` 可先做无副作用演练。实际回滚需显式设置 `EXECUTE_ROLLBACK=1` 和管理员凭证；接口只创建目标 cohort 的新 shadow 版本。紧急情况下把全局配置切到 `off`，下一个新请求恢复旧链路；不要删除迁移表或历史 run。

回滚后复核：新请求模式、旧请求不可变、SSE/JWT/审批/SQL guard 正常、无业务写、告警恢复。记录原因、policy version、开始和结束时间。
