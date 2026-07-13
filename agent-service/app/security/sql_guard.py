from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SqlPolicyError(ValueError):
    """Raised when generated SQL violates the read-only policy."""


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    tables: frozenset[str]
    columns: frozenset[str]
    limit: int


FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Into,
    exp.Lock,
)

# Unknown PostgreSQL functions can be backed by user-defined or SECURITY DEFINER
# functions. Built-in aggregates and scalar functions have dedicated sqlglot
# nodes; anonymous calls are denied unless explicitly reviewed here.
ALLOWED_ANONYMOUS_FUNCTIONS = frozenset()

DEFAULT_ALLOWED_TABLES = frozenset(
    {
        "customers",
        "products",
        "orders",
        "order_items",
        "refunds",
        "schema_catalog",
        "metric_definitions",
        "query_examples",
    }
)
DEFAULT_SENSITIVE_COLUMNS = frozenset(
    {"phone", "email", "password_hash", "approval_token_hash"}
)


def _literal_limit(expression: exp.Expression) -> int | None:
    limit_node = expression.args.get("limit")
    if not limit_node:
        return None
    value = limit_node.expression
    if not isinstance(value, exp.Literal) or not value.is_int:
        raise SqlPolicyError("LIMIT 必须是固定整数")
    return int(value.this)


def validate_read_query(
    sql: str,
    *,
    allowed_tables: frozenset[str] = DEFAULT_ALLOWED_TABLES,
    sensitive_columns: frozenset[str] = DEFAULT_SENSITIVE_COLUMNS,
    max_rows: int = 200,
) -> GuardedSql:
    if not sql or not sql.strip():
        raise SqlPolicyError("SQL 不能为空")

    try:
        statements = [statement for statement in parse(sql, read="postgres") if statement]
    except ParseError as exc:
        raise SqlPolicyError(f"SQL 无法解析: {exc}") from exc

    if len(statements) != 1:
        raise SqlPolicyError("只允许执行一条 SQL")

    expression = statements[0]
    if not isinstance(expression, exp.Query):
        raise SqlPolicyError("只允许 SELECT 或只读 CTE 查询")
    if any(expression.find(node_type) is not None for node_type in FORBIDDEN_NODES):
        raise SqlPolicyError("查询包含写操作、锁定、DDL 或数据库命令")

    anonymous_functions = {
        function.name.lower() for function in expression.find_all(exp.Anonymous)
    }
    blocked_functions = anonymous_functions - ALLOWED_ANONYMOUS_FUNCTIONS
    if blocked_functions:
        raise SqlPolicyError(
            f"函数不在白名单中: {', '.join(sorted(blocked_functions))}"
        )

    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in expression.find_all(exp.Table):
        if table.catalog:
            raise SqlPolicyError("禁止跨数据库查询")
        if table.db and table.db.lower() != "public":
            raise SqlPolicyError("只允许访问 public schema")
        name = table.name.lower()
        if name in cte_names and not table.db and not table.catalog:
            continue
        if name not in allowed_tables:
            raise SqlPolicyError(f"表不在白名单中: {name}")
        tables.add(name)

    if not tables:
        raise SqlPolicyError("查询必须访问已授权业务表")

    columns = {column.name.lower() for column in expression.find_all(exp.Column)}
    blocked = columns & sensitive_columns
    if blocked:
        raise SqlPolicyError(f"禁止访问敏感字段: {', '.join(sorted(blocked))}")

    contains_star = any(isinstance(node, exp.Star) for node in expression.walk())
    if contains_star and "customers" in tables:
        raise SqlPolicyError("customers 表禁止使用 SELECT *，请明确选择非敏感字段")

    requested_limit = _literal_limit(expression)
    if requested_limit is not None and (requested_limit < 1 or requested_limit > max_rows):
        raise SqlPolicyError(f"LIMIT 必须在 1 到 {max_rows} 之间")

    final_limit = requested_limit or max_rows
    if requested_limit is None:
        expression = expression.limit(final_limit)

    return GuardedSql(
        sql=expression.sql(dialect="postgres"),
        tables=frozenset(tables),
        columns=frozenset(columns),
        limit=final_limit,
    )
