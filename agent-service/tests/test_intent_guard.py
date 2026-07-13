import pytest

from app.security.intent_guard import QueryIntentError, validate_query_intent


def test_region_dimension_requires_group_by():
    with pytest.raises(QueryIntentError, match="按区域分组"):
        validate_query_intent(
            "SELECT SUM(r.amount) FROM refunds r JOIN orders o ON o.id = r.order_id",
            "每个区域的退款成功金额。",
        )


def test_region_dimension_accepts_selected_group():
    validate_query_intent(
        "SELECT o.region, SUM(r.amount) FROM refunds r JOIN orders o ON o.id = r.order_id GROUP BY o.region",
        "每个区域的退款成功金额。",
    )


def test_customer_dimension_requires_name_and_customers_join():
    with pytest.raises(QueryIntentError, match="customers"):
        validate_query_intent(
            "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id",
            "每个客户的已支付订单数。",
        )


def test_customer_dimension_requires_stable_name_alias():
    with pytest.raises(QueryIntentError, match="稳定输出别名 name"):
        validate_query_intent(
            "SELECT c.name AS customer_name, COUNT(o.id) AS paid_order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
            "每个客户的已支付订单数。",
        )


def test_customer_dimension_accepts_stable_name_alias():
    validate_query_intent(
        "SELECT c.name AS name, COUNT(o.id) AS paid_order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
        "每个客户的已支付订单数。",
    )


def test_non_dimension_question_is_unchanged():
    validate_query_intent("SELECT SUM(total_amount) FROM orders", "计算销售额。")
