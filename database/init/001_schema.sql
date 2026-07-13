CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE app_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('analyst', 'operator', 'admin')),
    email VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE customers (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    city VARCHAR(80) NOT NULL,
    region VARCHAR(32) NOT NULL,
    phone VARCHAR(32),
    email VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    category VARCHAR(80) NOT NULL,
    price NUMERIC(12,2) NOT NULL CHECK (price >= 0),
    stock INTEGER NOT NULL CHECK (stock >= 0),
    version BIGINT NOT NULL DEFAULT 0,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    status VARCHAR(24) NOT NULL CHECK (status IN ('DRAFT','UNPAID','PAID','SHIPPED','CANCELLED')),
    total_amount NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    region VARCHAR(32) NOT NULL,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version BIGINT NOT NULL DEFAULT 0,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE(order_id, product_id)
);

CREATE TABLE refunds (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    amount NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    status VARCHAR(24) NOT NULL CHECK (status IN ('PENDING','SUCCESS','REJECTED')),
    refunded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schema_catalog (
    id BIGSERIAL PRIMARY KEY,
    table_name VARCHAR(128) NOT NULL,
    column_name VARCHAR(128),
    description TEXT NOT NULL,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    is_sensitive BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE metric_definitions (
    id BIGSERIAL PRIMARY KEY,
    metric_name VARCHAR(128) NOT NULL UNIQUE,
    description TEXT NOT NULL,
    formula TEXT NOT NULL,
    example_sql TEXT NOT NULL
);

CREATE TABLE query_examples (
    id BIGSERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    sql_text TEXT NOT NULL,
    tags TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE pending_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES app_users(id),
    action_type VARCHAR(40) NOT NULL,
    normalized_payload JSONB NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    target_version BIGINT,
    impact_summary JSONB NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('PENDING','CONFIRMED','CANCELLED','EXPIRED','FAILED')),
    approval_token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    failure_reason TEXT
);

CREATE TABLE audit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES app_users(id),
    event_type VARCHAR(64) NOT NULL,
    request_id VARCHAR(64),
    original_instruction_hash VARCHAR(64),
    generated_sql TEXT,
    action_id UUID,
    success BOOLEAN NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    duration_ms BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_orders_created_at ON orders(created_at);
CREATE INDEX idx_orders_status_region ON orders(status, region);
CREATE INDEX idx_pending_actions_user_status ON pending_actions(user_id, status);
CREATE INDEX idx_audit_events_created_at ON audit_events(created_at);

INSERT INTO app_users(username, password_hash, role, email) VALUES
('analyst', crypt('analyst123', gen_salt('bf')), 'analyst', 'analyst@example.com'),
('operator', crypt('operator123', gen_salt('bf')), 'operator', 'operator@example.com'),
('admin', crypt('admin123', gen_salt('bf')), 'admin', 'admin@example.com');

INSERT INTO customers(name, city, region, phone, email) VALUES
('上海星辰商贸', '上海', '华东', '13800000001', 'shanghai@example.com'),
('杭州云帆科技', '杭州', '华东', '13800000002', 'hangzhou@example.com'),
('北京远景零售', '北京', '华北', '13800000003', 'beijing@example.com'),
('广州南风供应链', '广州', '华南', '13800000004', 'guangzhou@example.com');

INSERT INTO products(sku, name, category, price, stock) VALUES
('P-1001', '智能仓储标签', '仓储设备', 199.00, 500),
('P-1002', '工业扫码枪', '仓储设备', 1299.00, 120),
('P-1003', '包装耗材套装', '包装材料', 89.00, 1000),
('P-1004', '温湿度传感器', '物联网设备', 499.00, 300);

INSERT INTO orders(customer_id, status, total_amount, region, paid_at, created_at) VALUES
(1, 'PAID', 1498.00, '华东', now() - interval '5 days', now() - interval '6 days'),
(2, 'SHIPPED', 996.00, '华东', now() - interval '12 days', now() - interval '13 days'),
(3, 'PAID', 2598.00, '华北', now() - interval '20 days', now() - interval '21 days'),
(4, 'DRAFT', 178.00, '华南', NULL, now() - interval '1 day');

INSERT INTO order_items(order_id, product_id, quantity, unit_price) VALUES
(1, 1, 1, 199.00), (1, 2, 1, 1299.00),
(2, 4, 2, 498.00), (3, 2, 2, 1299.00),
(4, 3, 2, 89.00);

INSERT INTO refunds(order_id, amount, status, refunded_at) VALUES
(1, 199.00, 'SUCCESS', now() - interval '2 days'),
(3, 1299.00, 'PENDING', NULL);

INSERT INTO schema_catalog(table_name, column_name, description, aliases, is_sensitive) VALUES
('customers', 'id', '客户唯一标识', ARRAY['客户ID','用户ID'], false),
('customers', 'region', '客户所属大区', ARRAY['地区','区域'], false),
('customers', 'phone', '客户手机号', ARRAY['电话','手机'], true),
('customers', 'email', '客户邮箱', ARRAY['邮箱'], true),
('orders', 'status', '订单状态', ARRAY['订单状态'], false),
('orders', 'total_amount', '订单总金额', ARRAY['销售额','GMV'], false),
('orders', 'paid_at', '支付成功时间', ARRAY['付款时间'], false),
('refunds', 'status', '退款状态，SUCCESS 表示退款成功', ARRAY['退款结果'], false),
('refunds', 'amount', '退款金额', ARRAY['退款额'], false);

INSERT INTO schema_catalog(table_name, column_name, description, aliases, is_sensitive) VALUES
('customers', 'name', '客户名称', ARRAY['客户名','公司名称'], false),
('customers', 'city', '客户所在城市', ARRAY['城市'], false),
('products', 'id', '商品唯一标识', ARRAY['商品ID'], false),
('products', 'name', '商品名称', ARRAY['商品','产品'], false),
('products', 'category', '商品所属品类', ARRAY['品类','类别'], false),
('products', 'price', '商品当前单价', ARRAY['价格','售价'], false),
('products', 'stock', '商品可用库存', ARRAY['库存','余量'], false),
('orders', 'id', '订单唯一标识', ARRAY['订单ID'], false),
('orders', 'customer_id', '订单所属客户，关联 customers.id', ARRAY['客户ID'], false),
('orders', 'region', '订单所属区域', ARRAY['地区','大区'], false),
('orders', 'created_at', '订单创建时间，用于统计下单时间范围', ARRAY['下单时间','创建时间'], false),
('orders', 'deleted_at', '订单软删除时间，非空表示不参与业务统计', ARRAY['软删除'], false),
('order_items', 'order_id', '订单明细所属订单，关联 orders.id', ARRAY['订单ID'], false),
('order_items', 'product_id', '订单明细商品，关联 products.id', ARRAY['商品ID'], false),
('order_items', 'quantity', '订单明细购买数量', ARRAY['销量','件数'], false),
('order_items', 'unit_price', '下单时商品成交单价', ARRAY['成交价'], false),
('order_items', NULL, '订单明细表没有 deleted_at；过滤软删除订单时必须关联 orders 并使用 orders.deleted_at IS NULL', ARRAY['订单明细表','销量明细'], false),
('refunds', 'order_id', '退款所属订单，关联 orders.id', ARRAY['订单ID'], false),
('refunds', 'refunded_at', '退款成功时间', ARRAY['退款时间'], false);

INSERT INTO metric_definitions(metric_name, description, formula, example_sql) VALUES
('GMV', '已支付或已发货且未软删除订单的金额总和', 'SUM(orders.total_amount)', 'SELECT SUM(total_amount) FROM orders WHERE status IN (''PAID'',''SHIPPED'') AND deleted_at IS NULL'),
('退款率', '退款成功订单数除以支付成功订单数', 'COUNT(DISTINCT successful_refund.order_id) / COUNT(DISTINCT paid_order.id)', 'SELECT COUNT(DISTINCT r.order_id)::numeric / NULLIF(COUNT(DISTINCT o.id),0) FROM orders o LEFT JOIN refunds r ON r.order_id=o.id AND r.status=''SUCCESS'' WHERE o.status IN (''PAID'',''SHIPPED'')'),
('订单量', '未软删除订单的数量', 'COUNT(orders.id)', 'SELECT COUNT(*) AS order_count FROM orders WHERE deleted_at IS NULL'),
('商品销量', '订单明细数量之和，按商品或品类聚合', 'SUM(order_items.quantity)', 'SELECT p.name, SUM(oi.quantity) AS sales_quantity FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.deleted_at IS NULL GROUP BY p.name ORDER BY sales_quantity DESC');

INSERT INTO query_examples(question, sql_text, tags) VALUES
('最近30天华东地区销售额是多少', 'SELECT SUM(total_amount) AS sales FROM orders WHERE region=''华东'' AND status IN (''PAID'',''SHIPPED'') AND created_at >= now() - interval ''30 days'' AND deleted_at IS NULL', ARRAY['销售额','地区','时间']),
('退款率是多少', 'SELECT COUNT(DISTINCT r.order_id)::numeric / NULLIF(COUNT(DISTINCT o.id),0) AS refund_rate FROM orders o LEFT JOIN refunds r ON r.order_id=o.id AND r.status=''SUCCESS'' WHERE o.status IN (''PAID'',''SHIPPED'') AND o.deleted_at IS NULL', ARRAY['退款率']),
('各区域订单量', 'SELECT region, COUNT(*) AS order_count FROM orders WHERE deleted_at IS NULL GROUP BY region ORDER BY order_count DESC', ARRAY['订单量','区域']),
('销量最高的商品', 'SELECT p.name, SUM(oi.quantity) AS sales_quantity FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.deleted_at IS NULL GROUP BY p.name ORDER BY sales_quantity DESC LIMIT 10', ARRAY['Top N','商品销量']),
('各品类销售额', 'SELECT p.category, SUM(oi.quantity * oi.unit_price) AS sales_amount FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.status IN (''PAID'',''SHIPPED'') AND o.deleted_at IS NULL GROUP BY p.category ORDER BY sales_amount DESC', ARRAY['品类','销售额']),
('近30天退款金额', 'SELECT SUM(amount) AS refund_amount FROM refunds WHERE status=''SUCCESS'' AND refunded_at >= now() - interval ''30 days''', ARRAY['退款额','时间']),
('库存低于100的商品', 'SELECT id, name, stock FROM products WHERE stock < 100 AND deleted_at IS NULL ORDER BY stock ASC', ARRAY['库存','商品']);

DO $$
BEGIN
    -- Deterministic local-development credential. Production deployments must
    -- provision this role separately and rotate the password.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_readonly') THEN
        CREATE ROLE copilot_readonly LOGIN PASSWORD 'copilot_readonly_dev';
    END IF;
END $$;

GRANT CONNECT ON DATABASE enterprise_copilot TO copilot_readonly;
GRANT USAGE ON SCHEMA public TO copilot_readonly;
GRANT SELECT ON customers, products, orders, order_items, refunds,
    schema_catalog, metric_definitions, query_examples TO copilot_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO copilot_readonly;
