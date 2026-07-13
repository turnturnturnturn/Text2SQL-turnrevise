import pytest

from app.security.sql_guard import SqlPolicyError, validate_read_query


def test_select_is_normalized_and_limited():
    result = validate_read_query("SELECT id, status FROM orders")
    assert result.tables == frozenset({"orders"})
    assert result.limit == 200
    assert "LIMIT 200" in result.sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders; DELETE FROM orders",
        "UPDATE orders SET status='CANCELLED' WHERE id=1",
        "WITH changed AS (DELETE FROM orders RETURNING id) SELECT id FROM changed",
        "SELECT * FROM private.orders",
        "SELECT id FROM audit_events",
        "SELECT id INTO orders FROM products",
        "SELECT id FROM orders FOR UPDATE",
        "SELECT pg_read_file('/etc/passwd') FROM orders",
        "SELECT nextval('orders_id_seq') FROM orders",
    ],
)
def test_dangerous_or_unapproved_sql_is_rejected(sql):
    with pytest.raises(SqlPolicyError):
        validate_read_query(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT phone FROM customers",
        "SELECT email FROM customers",
        "SELECT * FROM customers",
        "SELECT customers.* FROM customers",
    ],
)
def test_sensitive_customer_data_is_rejected(sql):
    with pytest.raises(SqlPolicyError):
        validate_read_query(sql)


def test_excessive_limit_is_rejected():
    with pytest.raises(SqlPolicyError, match="LIMIT"):
        validate_read_query("SELECT id FROM orders LIMIT 201")


def test_read_only_cte_is_allowed():
    result = validate_read_query(
        "WITH recent AS (SELECT id, total_amount FROM orders WHERE deleted_at IS NULL) "
        "SELECT id FROM recent"
    )
    assert result.tables == frozenset({"orders"})


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(total_amount) FROM orders "
        "WHERE status IN ('PAID','SHIPPED') AND deleted_at IS NULL",
        "SELECT COUNT(DISTINCT r.order_id)::numeric / "
        "NULLIF(COUNT(DISTINCT o.id), 0) AS refund_rate "
        "FROM orders o LEFT JOIN refunds r "
        "ON r.order_id = o.id AND r.status = 'SUCCESS' "
        "WHERE o.status IN ('PAID','SHIPPED')",
        "SELECT DATE_TRUNC('month', created_at), AVG(total_amount) "
        "FROM orders GROUP BY 1",
    ],
)
def test_reviewed_analytics_functions_are_allowed(sql):
    assert validate_read_query(sql).tables
