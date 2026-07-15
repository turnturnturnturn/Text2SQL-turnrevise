from __future__ import annotations

from sqlglot import exp, parse_one

from app.grounding.catalog import CatalogSnapshot
from app.grounding.models import GroundingSnapshot
from app.grounding.query_plan import QueryPlanStatus, ValidatedQueryPlan


class QueryPlanAlignmentError(ValueError):
    """Raised when executable SQL diverges from its validated QueryPlan."""


def _operator_present(
    expression: exp.Expression,
    operator: str,
    target: tuple[str, str],
    aliases: dict[str, str],
) -> bool:
    return any(
        any(
            _column_matches(column, target, aliases)
            for column in node.find_all(exp.Column)
        )
        for node in _operator_nodes(expression, operator)
        if node.find_ancestor(exp.Where) is not None
        or node.find_ancestor(exp.Having) is not None
        if node.find_ancestor(exp.Or) is None
    )


def _operator_nodes(expression: exp.Expression, operator: str) -> list[exp.Expression]:
    binary_types = {
        "EQ": exp.EQ,
        "NE": exp.NEQ,
        "GT": exp.GT,
        "GTE": exp.GTE,
        "LT": exp.LT,
        "LTE": exp.LTE,
    }
    if operator in binary_types:
        return list(expression.find_all(binary_types[operator]))
    if operator == "IN":
        return [
            node
            for node in expression.find_all(exp.In)
            if not isinstance(node.parent, exp.Not)
        ]
    if operator == "NOT_IN":
        return [
            node.this
            for node in expression.find_all(exp.Not)
            if isinstance(node.this, exp.In)
        ]
    if operator == "BETWEEN":
        return list(expression.find_all(exp.Between))
    if operator == "IS_NULL":
        return [
            node
            for node in expression.find_all(exp.Is)
            if isinstance(node.expression, exp.Null)
            and not isinstance(node.parent, exp.Not)
        ]
    if operator == "IS_NOT_NULL":
        return [
            node.this
            for node in expression.find_all(exp.Not)
            if isinstance(node.this, exp.Is)
            and isinstance(node.this.expression, exp.Null)
        ]
    return []


def _column_matches(
    column: exp.Expression | None,
    target: tuple[str, str],
    aliases: dict[str, str],
) -> bool:
    if not isinstance(column, exp.Column):
        return False
    table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
    return (table, column.name.lower()) == target or (
        not table and column.name.lower() == target[1]
    )


def _predicate_literals_for_column(
    expression: exp.Expression,
    operator: str,
    target: tuple[str, str],
    aliases: dict[str, str],
) -> tuple[set[str], set[str]]:
    strings: set[str] = set()
    numbers: set[str] = set()
    for node in _operator_nodes(expression, operator):
        if (
            node.find_ancestor(exp.Where) is None
            and node.find_ancestor(exp.Having) is None
        ):
            continue
        if node.find_ancestor(exp.Or) is not None:
            continue
        if not any(
            _column_matches(column, target, aliases)
            for column in node.find_all(exp.Column)
        ):
            continue
        for literal in node.find_all(exp.Literal):
            if literal.is_string:
                strings.add(str(literal.this))
            elif literal.is_number:
                numbers.add(str(literal.this))
    return strings, numbers


def _asset_column(asset_id: str) -> tuple[str, str] | None:
    if not asset_id.startswith("column:"):
        return None
    parts = asset_id.removeprefix("column:").split(".")
    if len(parts) != 3:
        return None
    return parts[1].lower(), parts[2].lower()


def _asset_table(asset_id: str) -> str | None:
    if asset_id.startswith("table:"):
        return asset_id.rsplit(".", 1)[-1].lower()
    column = _asset_column(asset_id)
    return column[0] if column else None


def _canonical_expression(
    expression: exp.Expression,
    aliases: dict[str, str] | None = None,
    *,
    preserve_tables: bool = False,
) -> str:
    normalized = expression.copy()
    alias_map = aliases or {}
    physical_names = set(alias_map.values())
    for column in normalized.find_all(exp.Column):
        if preserve_tables:
            if column.table:
                table = alias_map.get(column.table.lower(), column.table.lower())
                column.set("table", exp.to_identifier(table))
            elif len(physical_names) == 1:
                column.set("table", exp.to_identifier(next(iter(physical_names))))
        else:
            column.set("table", None)
        column.set("db", None)
        column.set("catalog", None)
    for in_expression in normalized.find_all(exp.In):
        if in_expression.expressions:
            in_expression.set(
                "expressions",
                sorted(
                    in_expression.expressions,
                    key=lambda item: item.sql(dialect="postgres").upper(),
                ),
            )
    if isinstance(normalized, exp.EQ):
        sides = sorted(
            " ".join(side.sql(dialect="postgres").upper().split())
            for side in (normalized.left, normalized.right)
        )
        return f"{sides[0]} = {sides[1]}"
    return " ".join(normalized.sql(dialect="postgres").upper().split())


def _aggregate_signatures(expression: exp.Expression) -> set[str]:
    return {
        _canonical_expression(aggregate)
        for aggregate in expression.find_all(exp.AggFunc)
    }


def _predicate_signatures(
    where: exp.Expression | None,
    aliases: dict[str, str] | None = None,
    *,
    preserve_tables: bool = False,
) -> set[str]:
    if where is None:
        return set()

    def conjuncts(node: exp.Expression) -> list[exp.Expression]:
        if isinstance(node, exp.And):
            return [*conjuncts(node.left), *conjuncts(node.right)]
        return [node]

    root = where.this if isinstance(where, exp.Where) else where
    return {
        _canonical_expression(
            item, aliases, preserve_tables=preserve_tables
        )
        for item in conjuncts(root)
    }


def _query_predicate_signatures(
    expression: exp.Expression,
    aliases: dict[str, str] | None = None,
    *,
    preserve_tables: bool = False,
) -> set[str]:
    signatures = _predicate_signatures(
        expression.args.get("where"), aliases, preserve_tables=preserve_tables
    )
    signatures.update(
        _predicate_signatures(
            expression.args.get("having"), aliases, preserve_tables=preserve_tables
        )
    )
    for join in expression.find_all(exp.Join):
        signatures.update(
            _predicate_signatures(
                join.args.get("on"), aliases, preserve_tables=preserve_tables
            )
        )
    return signatures


def _query_predicate_columns(
    expression: exp.Expression, aliases: dict[str, str]
) -> set[tuple[str, str]]:
    predicates = [
        expression.args.get("where"),
        expression.args.get("having"),
        *(join.args.get("on") for join in expression.find_all(exp.Join)),
    ]
    physical_names = set(aliases.values())
    columns: set[tuple[str, str]] = set()
    for predicate in predicates:
        if predicate is None:
            continue
        for column in predicate.find_all(exp.Column):
            if column.table:
                table = aliases.get(column.table.lower(), column.table.lower())
            elif len(physical_names) == 1:
                table = next(iter(physical_names))
            else:
                table = ""
            columns.add((table, column.name.lower()))
    return columns


def _time_preset_present(
    where: exp.Expression | None,
    target: tuple[str, str],
    aliases: dict[str, str],
    preset: str,
    start=None,
    end=None,
) -> bool:
    if where is None:
        return False
    lower_bounds: list[str] = []
    upper_bounds: list[str] = []
    equalities: list[str] = []

    def contains_target(node: exp.Expression) -> bool:
        return any(
            _column_matches(column, target, aliases)
            for column in node.find_all(exp.Column)
        )

    for node in where.find_all(exp.EQ):
        if node.find_ancestor(exp.Or) is not None:
            continue
        if contains_target(node):
            equalities.append(node.sql(dialect="postgres"))
    for comparison in (exp.GT, exp.GTE, exp.LT, exp.LTE):
        for node in where.find_all(comparison):
            if node.find_ancestor(exp.Or) is not None:
                continue
            left_target = contains_target(node.left)
            right_target = contains_target(node.right)
            if left_target == right_target:
                continue
            is_greater = isinstance(node, (exp.GT, exp.GTE))
            if (left_target and is_greater) or (right_target and not is_greater):
                lower_bounds.append(node.sql(dialect="postgres"))
            else:
                upper_bounds.append(node.sql(dialect="postgres"))
    for node in where.find_all(exp.Between):
        if node.find_ancestor(exp.Or) is not None:
            continue
        if contains_target(node.this):
            rendered = node.sql(dialect="postgres")
            lower_bounds.append(rendered)
            upper_bounds.append(rendered)

    lower = " ".join(" ".join(lower_bounds).upper().split())
    upper = " ".join(" ".join(upper_bounds).upper().split())
    equality = " ".join(" ".join(equalities).upper().split())
    if not (lower or upper or equality):
        return False

    def has_current_boundary(text: str) -> bool:
        return any(
            token in text
            for token in ("CURRENT_DATE", "CURRENT_TIMESTAMP", "NOW()")
        )

    if preset == "TODAY":
        equality_is_date = (
            "CURRENT_DATE" in equality
            and (
                "DATE(" in equality
                or "::DATE" in equality
                or "DATE_TRUNC('DAY'" in equality
            )
        )
        return equality_is_date or (
            has_current_boundary(lower)
            and has_current_boundary(upper)
            and "+ INTERVAL" in upper
            and "'1" in upper
            and "DAY" in upper
        )
    if preset == "YESTERDAY":
        return (
            "CURRENT_DATE" in lower
            and "INTERVAL" in lower
            and "- INTERVAL" in lower
            and "'1" in lower
            and "DAY" in lower
            and "CURRENT_DATE" in upper
        )
    if preset in {"LAST_7_DAYS", "LAST_30_DAYS"}:
        days = "7" if preset == "LAST_7_DAYS" else "30"
        return (
            "INTERVAL" in lower
            and "- INTERVAL" in lower
            and f"'{days}" in lower
            and "DAY" in lower
            and has_current_boundary(lower)
            and has_current_boundary(upper)
        )
    if preset in {"THIS_MONTH", "LAST_MONTH"}:
        has_month = "DATE_TRUNC('MONTH'" in lower
        if preset == "THIS_MONTH":
            return (
                has_month
                and "DATE_TRUNC('MONTH'" in upper
                and "+ INTERVAL" in upper
                and "'1" in upper
                and "MONTH" in upper
            )
        return (
            has_month
            and "INTERVAL" in lower
            and "- INTERVAL" in lower
            and "'1" in lower
            and "MONTH" in lower
            and "DATE_TRUNC('MONTH'" in upper
        )
    if preset in {"THIS_QUARTER", "LAST_QUARTER"}:
        has_quarter = "DATE_TRUNC('QUARTER'" in lower
        upper_is_quarter = "DATE_TRUNC('QUARTER'" in upper
        if preset == "THIS_QUARTER":
            return (
                upper_is_quarter
                and has_quarter
                and "+ INTERVAL" in upper
                and (
                    ("'1" in upper and "QUARTER" in upper)
                    or ("'3" in upper and "MONTH" in upper)
                )
            )
        return (
            upper_is_quarter
            and has_quarter
            and "- INTERVAL" in lower
            and (
                ("'1" in lower and "QUARTER" in lower)
                or ("'3" in lower and "MONTH" in lower)
            )
        )
    if preset == "CUSTOM" and start is not None and end is not None:
        return start.date().isoformat() in lower and end.date().isoformat() in upper
    return False


def query_plan_alignment_errors(
    sql: str,
    validated: ValidatedQueryPlan | None,
    snapshot: GroundingSnapshot | None,
    catalog: CatalogSnapshot | None,
) -> list[str]:
    if validated is None or snapshot is None or catalog is None:
        return ["VALID_QUERY_PLAN_REQUIRED"]
    if validated.status != QueryPlanStatus.VALID:
        return [f"QUERY_PLAN_{validated.status.value}"]

    expression = parse_one(sql, read="postgres")
    cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    contains_cte = bool(cte_names)
    multi_scope = not isinstance(expression, exp.Select) or sum(
        1 for _ in expression.find_all(exp.Select)
    ) != 1

    def is_cte_reference(table: exp.Table) -> bool:
        name = table.name.lower()
        if table.db or table.catalog or name not in cte_names:
            return False
        containing_cte = table.find_ancestor(exp.CTE)
        # A same-named table inside the CTE's own non-recursive definition is
        # the physical base table, not a reference to the CTE being defined.
        return containing_cte is None or containing_cte.alias_or_name.lower() != name

    physical_tables = [
        table
        for table in expression.find_all(exp.Table)
        if not is_cte_reference(table)
    ]
    aliases = {
        (table.alias_or_name or table.name).lower(): table.name.lower()
        for table in physical_tables
    }
    actual_tables = {table.name.lower() for table in physical_tables}
    actual_columns = set()
    for column in expression.find_all(exp.Column):
        table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
        actual_columns.add((table, column.name.lower()))
    selected_by_alias = {
        selected.alias.lower(): selected
        for selected in expression.selects
        if selected.alias
    }

    assets = {item.asset_id: item for item in catalog.assets}
    values = {item.value_id: item for item in catalog.values}
    relations = {item.relation_id: item for item in catalog.relations}
    paths = {item.path_id: item for item in snapshot.join_paths}
    plan = validated.plan
    errors: list[str] = []
    if contains_cte:
        # QueryPlan v1 intentionally validates one flat SELECT scope. Reject
        # CTEs fail-closed instead of allowing an unused scope to "prove" a
        # metric, filter, or join that the result-producing scope does not use.
        errors.append("QUERY_PLAN_CTE_UNSUPPORTED")
    if multi_scope:
        errors.append("QUERY_PLAN_MULTISCOPE_UNSUPPORTED")
    planned_tables: set[str] = set()
    where = expression.args.get("where")
    actual_predicates = _query_predicate_signatures(
        expression, aliases, preserve_tables=True
    )
    where_columns = set()
    if where is not None:
        for column in where.find_all(exp.Column):
            table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
            where_columns.add((table, column.name.lower()))
    predicate_columns = _query_predicate_columns(expression, aliases)
    filter_columns = set(where_columns)
    having = expression.args.get("having")
    if having is not None:
        for column in having.find_all(exp.Column):
            table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
            filter_columns.add((table, column.name.lower()))
    allowed_predicate_columns = {
        column
        for column in (
            *(_asset_column(item.asset_id) for item in plan.filters),
            *(
                [_asset_column(plan.time_range.field_id)]
                if plan.time_range
                else []
            ),
        )
        if column is not None
    }

    referenced_columns = [
        *(item.asset_id for item in plan.dimensions),
        *(item.asset_id for item in plan.filters),
        *( [plan.time_range.field_id] if plan.time_range else [] ),
    ]
    for asset_id in referenced_columns:
        column = _asset_column(asset_id)
        if column is None:
            continue
        planned_tables.add(column[0])
        if column not in actual_columns and ("", column[1]) not in actual_columns:
            errors.append(f"PLANNED_COLUMN_MISSING:{asset_id}")

    for metric in plan.metrics:
        asset = assets.get(metric.id)
        if asset is None:
            continue
        example_sql = asset.payload.get("example_sql")
        if isinstance(example_sql, str):
            metric_expression = parse_one(example_sql, read="postgres")
            metric_tables = list(metric_expression.find_all(exp.Table))
            metric_aliases = {
                (table.alias_or_name or table.name).lower(): table.name.lower()
                for table in metric_tables
            }
            allowed_predicate_columns.update(
                _query_predicate_columns(metric_expression, metric_aliases)
            )
            planned_tables.update(table.name.lower() for table in metric_expression.find_all(exp.Table))
            metric_columns = {
                column.name.lower()
                for aggregate in metric_expression.find_all(exp.AggFunc)
                for column in aggregate.find_all(exp.Column)
            }
            metric_where = metric_expression.args.get("where")
            if metric_where is not None:
                metric_columns.update(
                    column.name.lower()
                    for column in metric_where.find_all(exp.Column)
                )
            actual_names = {column for _, column in actual_columns}
            if metric_columns and not metric_columns <= actual_names:
                errors.append(f"METRIC_COLUMNS_MISSING:{metric.id}")
            required_aggregates = _aggregate_signatures(metric_expression)
            selected_metric = selected_by_alias.get(metric.alias.lower())
            selected_metric_value = (
                selected_metric.this
                if isinstance(selected_metric, exp.Alias)
                else selected_metric
            )
            actual_metric_aggregates = (
                _aggregate_signatures(selected_metric)
                if selected_metric is not None
                else set()
            )
            required_output_expressions = {
                _canonical_expression(
                    selected.this if isinstance(selected, exp.Alias) else selected,
                    metric_aliases,
                    preserve_tables=True,
                )
                for selected in metric_expression.selects
                if any(True for _ in selected.find_all(exp.AggFunc))
            }
            if selected_metric is None:
                errors.append(f"METRIC_OUTPUT_MISSING:{metric.alias}")
            elif required_aggregates and not required_aggregates <= actual_metric_aggregates:
                errors.append(f"METRIC_AGGREGATE_MISMATCH:{metric.id}")
            elif (
                selected_metric_value is not None
                and required_output_expressions
                and _canonical_expression(
                    selected_metric_value, aliases, preserve_tables=True
                )
                not in required_output_expressions
            ):
                errors.append(f"METRIC_OUTPUT_EXPRESSION_MISMATCH:{metric.id}")
            required_predicates = _query_predicate_signatures(
                metric_expression, metric_aliases, preserve_tables=True
            )
            if required_predicates and not required_predicates <= actual_predicates:
                errors.append(f"METRIC_PREDICATE_MISMATCH:{metric.id}")

    for path_id in plan.join_path_ids:
        path = paths.get(path_id)
        if path is None:
            errors.append(f"JOIN_PATH_MISSING:{path_id}")
            continue
        planned_tables.update(filter(None, (_asset_table(item) for item in path.table_asset_ids)))
        for relation_id in path.relation_ids:
            relation = relations.get(relation_id)
            if relation is None:
                errors.append(f"JOIN_RELATION_MISSING:{relation_id}")
                continue
            left = _asset_column(relation.left_asset_id)
            right = _asset_column(relation.right_asset_id)
            if left is None or right is None:
                continue
            allowed_predicate_columns.update((left, right))
            found = False
            for join in expression.find_all(exp.Join):
                on = join.args.get("on")
                if on is None:
                    continue
                if join.args.get("side") or join.args.get("kind") not in (None, "INNER"):
                    continue
                for equality in on.find_all(exp.EQ):
                    if equality.find_ancestor(exp.Or) is not None:
                        continue
                    if not isinstance(equality.left, exp.Column) or not isinstance(equality.right, exp.Column):
                        continue

                    def resolved(column: exp.Column) -> tuple[str, str]:
                        table = aliases.get(column.table.lower(), column.table.lower())
                        return table, column.name.lower()

                    pair = {resolved(equality.left), resolved(equality.right)}
                    if pair == {left, right}:
                        found = True
                        break
                if found:
                    break
            if not found:
                errors.append(f"CONFIRMED_JOIN_MISSING:{relation_id}")

    unexpected_predicate_columns = {
        column
        for column in predicate_columns
        if column not in allowed_predicate_columns
        and not (
            column[0] == ""
            and any(column[1] == allowed[1] for allowed in allowed_predicate_columns)
        )
    }
    if unexpected_predicate_columns:
        errors.append(
            f"UNPLANNED_PREDICATE_COLUMNS:{sorted(unexpected_predicate_columns)}"
        )

    unexpected_tables = actual_tables - planned_tables
    if unexpected_tables:
        errors.append(f"UNPLANNED_TABLES:{sorted(unexpected_tables)}")

    for clause in plan.filters:
        column = _asset_column(clause.asset_id)
        if column and column not in filter_columns and ("", column[1]) not in filter_columns:
            errors.append(f"PLANNED_FILTER_MISSING:{clause.asset_id}")
        if column and not _operator_present(expression, clause.op.value, column, aliases):
            errors.append(f"PLANNED_OPERATOR_MISSING:{clause.asset_id}:{clause.op.value}")
        string_literals, numeric_literals = (
            _predicate_literals_for_column(
                expression, clause.op.value, column, aliases
            )
            if column
            else (set(), set())
        )
        planned_strings = {
            values[value_id].canonical_value
            for value_id in clause.value_ids
            if value_id in values
        }
        for value_id in clause.value_ids:
            value = values.get(value_id)
            if value is not None and value.canonical_value not in string_literals:
                errors.append(f"PLANNED_VALUE_MISSING:{value_id}")
        if clause.value_ids and string_literals - planned_strings:
            errors.append(f"UNPLANNED_FILTER_VALUES:{clause.asset_id}")
        for value in clause.literal_values:
            if str(value) not in numeric_literals:
                errors.append(f"PLANNED_LITERAL_MISSING:{clause.asset_id}:{value}")
        planned_numbers = {str(value) for value in clause.literal_values}
        if clause.literal_values and numeric_literals - planned_numbers:
            errors.append(f"UNPLANNED_FILTER_LITERALS:{clause.asset_id}")

    if plan.time_range:
        time_column = _asset_column(plan.time_range.field_id)
        if time_column is None:
            errors.append(f"INVALID_TIME_FIELD:{plan.time_range.field_id}")
        elif time_column not in where_columns and ("", time_column[1]) not in where_columns:
            errors.append(f"PLANNED_TIME_FILTER_MISSING:{plan.time_range.field_id}")
        if time_column is None or not _time_preset_present(
                where,
                time_column,
                aliases,
                plan.time_range.preset.value,
                plan.time_range.start,
                plan.time_range.end,
            ):
            errors.append(f"TIME_PRESET_MISMATCH:{plan.time_range.preset.value}")

    group = expression.args.get("group")
    grouped = {
        (aliases.get(column.table.lower(), column.table.lower()) if column.table else "", column.name.lower())
        for column in group.find_all(exp.Column)
    } if group else set()
    selected = {
        (aliases.get(column.table.lower(), column.table.lower()) if column.table else "", column.name.lower())
        for select in expression.selects for column in select.find_all(exp.Column)
    }
    for grain_id in plan.grain:
        column = _asset_column(grain_id)
        if column and column not in grouped and ("", column[1]) not in grouped:
            errors.append(f"GRAIN_NOT_GROUPED:{grain_id}")
        if column and column not in selected and ("", column[1]) not in selected:
            errors.append(f"GRAIN_NOT_SELECTED:{grain_id}")

    planned_outputs = {
        *(item.alias.lower() for item in plan.metrics),
        *(item.alias.lower() for item in plan.dimensions),
    }
    actual_outputs = {
        item.alias_or_name.lower()
        for item in expression.selects
        if item.alias_or_name
    }
    if actual_outputs != planned_outputs:
        errors.append(
            f"QUERY_OUTPUT_MISMATCH:expected={sorted(planned_outputs)},actual={sorted(actual_outputs)}"
        )
    output_by_name = {
        item.alias_or_name.lower(): item
        for item in expression.selects
        if item.alias_or_name
    }
    for dimension in plan.dimensions:
        expected_column = _asset_column(dimension.asset_id)
        selected_output = output_by_name.get(dimension.alias.lower())
        if expected_column is None or selected_output is None:
            continue
        value_expression = (
            selected_output.this
            if isinstance(selected_output, exp.Alias)
            else selected_output
        )
        if not isinstance(value_expression, exp.Column) or not _column_matches(
            value_expression, expected_column, aliases
        ):
            errors.append(f"DIMENSION_OUTPUT_MISMATCH:{dimension.asset_id}")

    order = expression.args.get("order")
    actual_sort: list[tuple[str, str]] = []
    if order is not None:
        for ordered in order.expressions:
            field = ordered.this.alias_or_name
            if field:
                direction = "DESC" if ordered.args.get("desc") else "ASC"
                actual_sort.append((field.lower(), direction))
    planned_sort = [
        (item.field.lower(), item.direction.value) for item in plan.sort
    ]
    if actual_sort != planned_sort:
        errors.append(
            f"QUERY_SORT_MISMATCH:expected={planned_sort},actual={actual_sort}"
        )

    limit = expression.args.get("limit")
    if limit is None:
        errors.append("QUERY_LIMIT_REQUIRED")
    elif isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        if int(limit.expression.this) > plan.limit:
            errors.append("QUERY_LIMIT_EXCEEDS_PLAN")
    else:
        errors.append("QUERY_LIMIT_INVALID")
    return sorted(set(errors))


def validate_sql_against_query_plan(
    sql: str,
    validated: ValidatedQueryPlan | None,
    snapshot: GroundingSnapshot | None,
    catalog: CatalogSnapshot | None,
) -> None:
    errors = query_plan_alignment_errors(sql, validated, snapshot, catalog)
    if errors:
        raise QueryPlanAlignmentError("; ".join(errors))
