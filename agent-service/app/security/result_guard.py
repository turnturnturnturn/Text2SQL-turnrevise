from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class QueryResultIntentError(ValueError):
    """Raised when successful-event aggregation leaks empty dimension groups."""


def _is_empty_metric(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def validate_result_intent(
    records: list[dict[str, Any]], instruction: str | None
) -> None:
    if not instruction or any(
        phrase in instruction for phrase in ("包括零", "包括没有", "即使没有", "所有客户")
    ):
        return

    if "已支付订单" in instruction and any(term in instruction for term in ("每个客户", "各客户", "按客户")):
        count_values = [
            value
            for row in records
            for column, value in row.items()
            if "count" in column.lower() or "数量" in column
        ]
        if any(_is_empty_metric(value) for value in count_values):
            raise QueryResultIntentError(
                "结果包含没有已支付订单的客户；请使用匹配已支付/已发货订单的 INNER JOIN 或 HAVING COUNT(...) > 0，并只返回有结果的客户。"
            )

    if "退款成功金额" in instruction and any(term in instruction for term in ("每个区域", "各区域", "按区域")):
        amount_values = [
            value
            for row in records
            for column, value in row.items()
            if "amount" in column.lower() or "金额" in column
        ]
        if any(_is_empty_metric(value) for value in amount_values):
            raise QueryResultIntentError(
                "结果包含没有成功退款的空区域分组；请使用成功退款 INNER JOIN/WHERE 条件，只返回存在成功退款的区域。"
            )
