from __future__ import annotations

import re

from sqlglot import exp, parse_one


class QueryIntentError(ValueError):
    """Raised when executable SQL omits an explicit question dimension."""


DIMENSION_RULES = (
    (
        re.compile(r"(?:按|每个|各)(?:区域|地区|大区)"),
        "region",
        None,
        "问题要求按区域分组；请在 SELECT 和 GROUP BY 中包含 region。",
    ),
    (
        re.compile(r"(?:按|每个|各)(?:客户|用户)"),
        "name",
        "customers",
        "问题要求按客户展示；请关联 customers，并在 SELECT 和 GROUP BY 中包含 customers.name。",
    ),
    (
        re.compile(r"(?:按|每个|各|商品)(?:品类|类别)"),
        "category",
        "products",
        "问题要求按商品品类分组；请关联 products，并在 SELECT 和 GROUP BY 中包含 products.category。",
    ),
)


def validate_query_intent(sql: str, instruction: str | None) -> None:
    """Give the LLM deterministic feedback for explicit grouping dimensions."""
    if not instruction:
        return
    expression = parse_one(sql, read="postgres")
    group = expression.args.get("group")
    grouped_columns = (
        {column.name.lower() for column in group.find_all(exp.Column)} if group else set()
    )
    selected_columns = {
        column.name.lower() for select in expression.selects for column in select.find_all(exp.Column)
    }
    output_names = {select.alias_or_name.lower() for select in expression.selects}
    tables = {table.name.lower() for table in expression.find_all(exp.Table)}

    for pattern, required_column, required_table, message in DIMENSION_RULES:
        if not pattern.search(instruction):
            continue
        if required_table and required_table not in tables:
            raise QueryIntentError(message)
        if required_column not in grouped_columns or required_column not in selected_columns:
            raise QueryIntentError(message)
        if required_table == "customers" and "name" not in output_names:
            raise QueryIntentError(
                "客户名称必须使用稳定输出别名 name；请写成 customers.name AS name。"
            )
