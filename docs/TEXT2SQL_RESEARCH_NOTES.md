# Text2SQL 研究与工程优化记录（2026-07）

## 本轮采用的研究结论

- **召回导向的 Schema Linking**：小模型更依赖高召回、低噪声的 Schema 上下文。EMNLP 2025 Industry 的 [Divide, Link, and Conquer](https://aclanthology.org/2025.emnlp-industry.122/) 将问题分解用于 Schema Linking，在 BIRD 上报告了 Schema Recall 与执行准确率提升。
- **Schema Graph 路径扩展**：[SchemaGraphSQL](https://arxiv.org/abs/2505.18363) 使用外键关系图和路径搜索补齐连接表；这与本项目在客户、订单、商品、退款间自动补充 Join Path 的需求一致。
- **执行反馈自纠**：Findings EACL 2026 的 [LitE-SQL](https://aclanthology.org/2026.findings-eacl.186/) 将向量 Schema Linking 与执行引导自纠结合；ACL 2026 的 [ReEx-SQL](https://aclanthology.org/2026.acl-long.35/) 进一步强调在推理过程中利用执行反馈。
- **表优先与列优先的双向检索**：[Rethinking Schema Linking](https://aclanthology.org/2026.findings-eacl.236/) 表明 Schema Linking 应独立优化，并结合表级与列级上下文，减少漏召回和无关字段干扰。

## 已落地

- 混合关键词/BGE 检索后，根据客户、商品、品类、销量和退款意图自动追加确定性的外键 Join Path。
- 对“按区域、客户、品类”等显式维度，在执行前验证 SELECT、GROUP BY 和必要关联表；不满足时向 Qwen 返回可修正反馈。
- 对已支付订单和成功退款等成功事件聚合，在执行后识别空值/零值维度组，引导模型改用 INNER JOIN、WHERE 或 HAVING。
- 为返回字段定义稳定别名契约，避免同一指标在接口中出现随机列名。
- 保留 SQL AST、只读账户、审批和审计边界；反馈层不能绕过原有安全策略。

## 实测变化

在未修改的 10 条查询、10 条危险请求和同一 Qwen3-4B 模型上：

| 指标 | 优化前 | 优化后（已验证） |
| --- | ---: | ---: |
| SQL 执行成功率 | 100% | 100% |
| 严格列和值等价率 | 50% | 90% |
| 危险请求无执行率 | 100% | 100% |
| 审计哈希关联率 | 100% | 100% |

## 后续优先级

1. **Value Linking**：为状态枚举、地区、品类和产品名称建立受控样例值索引，解决问题实体与数据库真实值不一致。
2. **表级→列级双阶段检索**：先召回目标表和 Join 子图，再在子图内选择列，降低 Top-K 中无关指标干扰。
3. **保守式候选验证**：只在高不确定性问题上生成 2 个候选 SQL，以执行结果、维度覆盖和安全策略选择，不对所有请求增加延迟。
4. **难负例训练**：记录相似但错误的字段、Join 和状态条件，后续对嵌入模型或小型 Schema Linker 做难负例微调。
5. **Gold Query 中间表示**：先生成指标、维度、过滤器、时间范围和排序的结构化计划，再编译 SQL，提高可解释性与修正粒度。
