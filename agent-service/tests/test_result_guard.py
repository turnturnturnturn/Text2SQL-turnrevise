import pytest

from app.security.result_guard import QueryResultIntentError, validate_result_intent


def test_paid_customer_result_rejects_zero_groups():
    with pytest.raises(QueryResultIntentError, match="没有已支付订单"):
        validate_result_intent(
            [{"name": "广州南风供应链", "paid_order_count": 0}],
            "每个客户的已支付订单数。",
        )


def test_refund_region_result_rejects_null_groups():
    with pytest.raises(QueryResultIntentError, match="空区域分组"):
        validate_result_intent(
            [{"region": "华北", "refund_amount": None}],
            "每个区域的退款成功金额。",
        )


def test_explicit_zero_group_request_is_allowed():
    validate_result_intent(
        [{"name": "广州南风供应链", "paid_order_count": 0}],
        "所有客户的已支付订单数，包括零订单客户。",
    )


def test_success_only_nonempty_results_are_allowed():
    validate_result_intent(
        [{"region": "华东", "refund_amount": "199.00"}],
        "每个区域的退款成功金额。",
    )
