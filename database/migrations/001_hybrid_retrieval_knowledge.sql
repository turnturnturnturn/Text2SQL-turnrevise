-- Safe to run repeatedly against an existing local demo database.  This only
-- adds or refreshes curated retrieval knowledge; it never changes business data.
INSERT INTO schema_catalog(table_name, column_name, description, aliases, is_sensitive)
SELECT table_name, column_name, description, aliases, is_sensitive
FROM (VALUES
  ('customers', 'name', '客户名称', ARRAY['客户名','公司名称']::TEXT[], false),
  ('customers', 'city', '客户所在城市', ARRAY['城市']::TEXT[], false),
  ('products', 'id', '商品唯一标识', ARRAY['商品ID']::TEXT[], false),
  ('products', 'name', '商品名称', ARRAY['商品','产品']::TEXT[], false),
  ('products', 'category', '商品所属品类', ARRAY['品类','类别']::TEXT[], false),
  ('products', 'price', '商品当前单价', ARRAY['价格','售价']::TEXT[], false),
  ('products', 'stock', '商品可用库存', ARRAY['库存','余量']::TEXT[], false),
  ('orders', 'id', '订单唯一标识', ARRAY['订单ID']::TEXT[], false),
  ('orders', 'customer_id', '订单所属客户，关联 customers.id', ARRAY['客户ID']::TEXT[], false),
  ('orders', 'region', '订单所属区域', ARRAY['地区','大区']::TEXT[], false),
  ('orders', 'created_at', '订单创建时间，用于统计下单时间范围', ARRAY['下单时间','创建时间']::TEXT[], false),
  ('orders', 'deleted_at', '订单软删除时间，非空表示不参与业务统计', ARRAY['软删除']::TEXT[], false),
  ('order_items', 'order_id', '订单明细所属订单，关联 orders.id', ARRAY['订单ID']::TEXT[], false),
  ('order_items', 'product_id', '订单明细商品，关联 products.id', ARRAY['商品ID']::TEXT[], false),
  ('order_items', 'quantity', '订单明细购买数量', ARRAY['销量','件数']::TEXT[], false),
  ('order_items', 'unit_price', '下单时商品成交单价', ARRAY['成交价']::TEXT[], false),
  ('order_items', NULL, '订单明细表没有 deleted_at；过滤软删除订单时必须关联 orders 并使用 orders.deleted_at IS NULL', ARRAY['订单明细表','销量明细']::TEXT[], false),
  ('refunds', 'order_id', '退款所属订单，关联 orders.id', ARRAY['订单ID']::TEXT[], false),
  ('refunds', 'refunded_at', '退款成功时间', ARRAY['退款时间']::TEXT[], false)
) AS source(table_name, column_name, description, aliases, is_sensitive)
WHERE NOT EXISTS (
  SELECT 1 FROM schema_catalog current
  WHERE current.table_name = source.table_name
    AND current.column_name IS NOT DISTINCT FROM source.column_name
);

INSERT INTO metric_definitions(metric_name, description, formula, example_sql) VALUES
('订单量', '未软删除订单的数量', 'COUNT(orders.id)', 'SELECT COUNT(*) AS order_count FROM orders WHERE deleted_at IS NULL'),
('商品销量', '订单明细数量之和，按商品或品类聚合', 'SUM(order_items.quantity)', 'SELECT p.name, SUM(oi.quantity) AS sales_quantity FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.deleted_at IS NULL GROUP BY p.name ORDER BY sales_quantity DESC')
ON CONFLICT (metric_name) DO UPDATE SET
  description = EXCLUDED.description,
  formula = EXCLUDED.formula,
  example_sql = EXCLUDED.example_sql;

INSERT INTO query_examples(question, sql_text, tags)
SELECT question, sql_text, tags
FROM (VALUES
  ('各区域订单量', 'SELECT region, COUNT(*) AS order_count FROM orders WHERE deleted_at IS NULL GROUP BY region ORDER BY order_count DESC', ARRAY['订单量','区域']::TEXT[]),
  ('销量最高的商品', 'SELECT p.name, SUM(oi.quantity) AS sales_quantity FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.deleted_at IS NULL GROUP BY p.name ORDER BY sales_quantity DESC LIMIT 10', ARRAY['Top N','商品销量']::TEXT[]),
  ('各品类销售额', 'SELECT p.category, SUM(oi.quantity * oi.unit_price) AS sales_amount FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.status IN (''PAID'',''SHIPPED'') AND o.deleted_at IS NULL GROUP BY p.category ORDER BY sales_amount DESC', ARRAY['品类','销售额']::TEXT[]),
  ('近30天退款金额', 'SELECT SUM(amount) AS refund_amount FROM refunds WHERE status=''SUCCESS'' AND refunded_at >= now() - interval ''30 days''', ARRAY['退款额','时间']::TEXT[]),
  ('库存低于100的商品', 'SELECT id, name, stock FROM products WHERE stock < 100 AND deleted_at IS NULL ORDER BY stock ASC', ARRAY['库存','商品']::TEXT[])
) AS source(question, sql_text, tags)
WHERE NOT EXISTS (SELECT 1 FROM query_examples current WHERE current.question = source.question);
