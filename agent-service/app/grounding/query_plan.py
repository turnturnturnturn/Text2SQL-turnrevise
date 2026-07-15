from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator
from sqlglot import exp, parse_one

from app.grounding.catalog import CatalogSnapshot
from app.grounding.models import (
    AssetStatus,
    AssetType,
    GroundingSnapshot,
    RelationStatus,
    Sensitivity,
)


class FilterOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    BETWEEN = "BETWEEN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class TimePreset(StrEnum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    LAST_7_DAYS = "LAST_7_DAYS"
    LAST_30_DAYS = "LAST_30_DAYS"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_QUARTER = "THIS_QUARTER"
    LAST_QUARTER = "LAST_QUARTER"
    CUSTOM = "CUSTOM"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class QueryPlanStatus(StrEnum):
    VALID = "VALID"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED = "BLOCKED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MetricRef(StrictModel):
    id: str = Field(min_length=1, max_length=320)
    alias: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class DimensionRef(StrictModel):
    asset_id: str = Field(min_length=1, max_length=320)
    alias: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class FilterClause(StrictModel):
    asset_id: str = Field(min_length=1, max_length=320)
    op: FilterOperator
    value_ids: list[str] = Field(default_factory=list, max_length=20)
    literal_values: list[StrictInt | StrictFloat] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_values(self):
        null_ops = {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}
        if self.op in null_ops and (self.value_ids or self.literal_values):
            raise ValueError("null filters cannot contain values")
        if self.op not in null_ops and not (self.value_ids or self.literal_values):
            raise ValueError("filter requires value_ids or literal_values")
        if self.value_ids and self.literal_values:
            raise ValueError("use value_ids or literal_values, not both")
        if self.op == FilterOperator.BETWEEN and len(self.literal_values) != 2:
            raise ValueError("BETWEEN requires exactly two literal values")
        single_value_ops = {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
        }
        if self.op in single_value_ops and len(self.value_ids or self.literal_values) != 1:
            raise ValueError(f"{self.op.value} requires exactly one value")
        return self


class TimeRange(StrictModel):
    field_id: str = Field(min_length=1, max_length=320)
    preset: TimePreset
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.preset == TimePreset.CUSTOM:
            if self.start is None or self.end is None:
                raise ValueError("CUSTOM time range requires start and end")
            if self.start.tzinfo is None or self.end.tzinfo is None:
                raise ValueError("CUSTOM timestamps must include timezone")
            if self.end <= self.start:
                raise ValueError("time range end must be later than start")
        elif self.start is not None or self.end is not None:
            raise ValueError("preset time range cannot contain custom timestamps")
        return self


class SortClause(StrictModel):
    field: str = Field(min_length=1, max_length=80)
    direction: SortDirection


class PlanAmbiguity(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=300)
    option_ids: list[str] = Field(default_factory=list, min_length=2, max_length=3)


class QueryPlanDraft(StrictModel):
    metrics: list[MetricRef] = Field(default_factory=list, max_length=8)
    dimensions: list[DimensionRef] = Field(default_factory=list, max_length=12)
    filters: list[FilterClause] = Field(default_factory=list, max_length=20)
    time_range: TimeRange | None = None
    grain: list[str] = Field(default_factory=list, max_length=12)
    sort: list[SortClause] = Field(default_factory=list, max_length=8)
    limit: int = Field(default=200, ge=1, le=200)
    join_path_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(default_factory=list, min_length=1, max_length=40)
    ambiguities: list[PlanAmbiguity] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_references(self):
        aliases = [item.alias for item in (*self.metrics, *self.dimensions)]
        if len(aliases) != len(set(aliases)):
            raise ValueError("metric and dimension aliases must be unique")
        valid_sort_fields = set(aliases)
        unknown_sort = {item.field for item in self.sort} - valid_sort_fields
        if unknown_sort:
            raise ValueError(f"sort fields must reference output aliases: {sorted(unknown_sort)}")
        dimension_ids = {item.asset_id for item in self.dimensions}
        if set(self.grain) - dimension_ids:
            raise ValueError("grain must reference declared dimension asset ids")
        return self


class ValidatedQueryPlan(StrictModel):
    status: QueryPlanStatus
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_version: int = 1
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    errors: list[str]
    plan: QueryPlanDraft


class QueryPlanValidator:
    def validate(
        self,
        draft: QueryPlanDraft,
        snapshot: GroundingSnapshot,
        catalog: CatalogSnapshot,
    ) -> ValidatedQueryPlan:
        assets = {item.asset_id: item for item in catalog.assets}
        values = {item.value_id: item for item in catalog.values}
        controlled_columns = {item.column_asset_id for item in catalog.values}
        relations = {item.relation_id: item for item in catalog.relations}
        available_evidence = set(snapshot.evidence_ids)
        paths = {item.path_id: item for item in snapshot.join_paths}
        errors: list[str] = []
        referenced_table_ids: set[str] = set()
        required_evidence: set[str] = set()
        effective_at = datetime.now(timezone.utc)

        def is_current(item) -> bool:
            return (
                item.status == AssetStatus.PUBLISHED
                and item.sensitivity != Sensitivity.RESTRICTED
                and (item.effective_from is None or item.effective_from <= effective_at)
                and (item.effective_to is None or item.effective_to > effective_at)
            )

        def add_asset_table(asset_id: str) -> None:
            asset = assets.get(asset_id)
            if asset is None:
                return
            table_name = asset.payload.get("table_name")
            schema = asset.payload.get("database_schema", "public")
            if isinstance(table_name, str):
                referenced_table_ids.add(f"table:{schema}.{table_name}")

        def require_asset(asset_id: str, expected: AssetType) -> None:
            asset = assets.get(asset_id)
            if asset is None or asset.asset_type != expected:
                errors.append(f"UNKNOWN_{expected.value}:{asset_id}")
            elif not is_current(asset):
                errors.append(f"INELIGIBLE_{expected.value}:{asset_id}")
            elif asset_id not in available_evidence:
                errors.append(f"ASSET_NOT_GROUNDED:{asset_id}")

        for metric in draft.metrics:
            required_evidence.add(metric.id)
            require_asset(metric.id, AssetType.METRIC)
            metric_asset = assets.get(metric.id)
            example_sql = metric_asset.payload.get("example_sql") if metric_asset else None
            if isinstance(example_sql, str):
                try:
                    expression = parse_one(example_sql, read="postgres")
                    referenced_table_ids.update(
                        f"table:public.{table.name.lower()}"
                        for table in expression.find_all(exp.Table)
                    )
                except Exception:
                    errors.append(f"METRIC_EXAMPLE_INVALID:{metric.id}")
        for dimension in draft.dimensions:
            required_evidence.add(dimension.asset_id)
            require_asset(dimension.asset_id, AssetType.COLUMN)
            add_asset_table(dimension.asset_id)
        for clause in draft.filters:
            required_evidence.add(clause.asset_id)
            required_evidence.update(clause.value_ids)
            require_asset(clause.asset_id, AssetType.COLUMN)
            add_asset_table(clause.asset_id)
            clause_asset = assets.get(clause.asset_id)
            column_name = (
                str(clause_asset.payload.get("column_name", "")).lower()
                if clause_asset is not None
                else ""
            )
            if clause.literal_values and (
                column_name == "id" or column_name.endswith("_id")
            ):
                errors.append(f"IDENTIFIER_LITERAL_FORBIDDEN:{clause.asset_id}")
            if clause.asset_id in controlled_columns and clause.literal_values:
                errors.append(f"CONTROLLED_VALUE_ID_REQUIRED:{clause.asset_id}")
            for value_id in clause.value_ids:
                value = values.get(value_id)
                if value is None or not is_current(value) or value_id not in available_evidence:
                    errors.append(f"VALUE_NOT_GROUNDED:{value_id}")
                elif value.column_asset_id != clause.asset_id:
                    errors.append(f"VALUE_COLUMN_MISMATCH:{value_id}")
        if draft.time_range:
            required_evidence.add(draft.time_range.field_id)
            require_asset(draft.time_range.field_id, AssetType.COLUMN)
            add_asset_table(draft.time_range.field_id)

        selected_path_tables: set[str] = set()
        path_graph: dict[str, set[str]] = {}
        for path_id in draft.join_path_ids:
            path = paths.get(path_id)
            if path is None:
                errors.append(f"JOIN_PATH_NOT_GROUNDED:{path_id}")
                continue
            endpoints = {
                path.table_asset_ids[0],
                path.table_asset_ids[-1],
            } if path.table_asset_ids else set()
            if not endpoints or not endpoints <= referenced_table_ids:
                errors.append(f"UNNEEDED_JOIN_PATH:{path_id}")
            selected_path_tables.update(path.table_asset_ids)
            for left_table, right_table in zip(
                path.table_asset_ids, path.table_asset_ids[1:]
            ):
                path_graph.setdefault(left_table, set()).add(right_table)
                path_graph.setdefault(right_table, set()).add(left_table)
            for relation_id in path.relation_ids:
                required_evidence.add(relation_id)
                relation = relations.get(relation_id)
                endpoints = (
                    assets.get(relation.left_asset_id),
                    assets.get(relation.right_asset_id),
                ) if relation is not None else (None, None)
                if (
                    relation is None
                    or relation.status != RelationStatus.CONFIRMED
                    or relation.sensitivity == Sensitivity.RESTRICTED
                    or any(endpoint is None or not is_current(endpoint) for endpoint in endpoints)
                ):
                    errors.append(f"JOIN_NOT_CONFIRMED:{relation_id}")
        if len(referenced_table_ids) > 1:
            missing_path_tables = referenced_table_ids - selected_path_tables
            if missing_path_tables:
                errors.append(f"JOIN_PATH_REQUIRED:{sorted(missing_path_tables)}")
        if selected_path_tables:
            reachable: set[str] = set()
            pending = [next(iter(selected_path_tables))]
            while pending:
                table_id = pending.pop()
                if table_id in reachable:
                    continue
                reachable.add(table_id)
                pending.extend(path_graph.get(table_id, set()) - reachable)
            disconnected = selected_path_tables - reachable
            if disconnected:
                errors.append(f"DISCONNECTED_JOIN_PATHS:{sorted(disconnected)}")

        ungrounded_evidence = set(draft.evidence_ids) - available_evidence
        if ungrounded_evidence:
            errors.append(f"EVIDENCE_NOT_GROUNDED:{sorted(ungrounded_evidence)}")
        missing_evidence = required_evidence - set(draft.evidence_ids)
        if missing_evidence:
            errors.append(f"REQUIRED_EVIDENCE_MISSING:{sorted(missing_evidence)}")

        canonical = draft.model_dump(mode="json", exclude_none=True)
        plan_hash = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        ambiguity_codes = [item.code for item in draft.ambiguities]
        ambiguity_codes.extend(snapshot.ambiguities)
        if errors:
            status = QueryPlanStatus.BLOCKED
        elif ambiguity_codes:
            status = QueryPlanStatus.NEEDS_CLARIFICATION
        else:
            status = QueryPlanStatus.VALID

        evidence_coverage = len(required_evidence & set(draft.evidence_ids)) / max(
            len(required_evidence), 1
        )
        confidence = round(snapshot.confidence * (0.75 + evidence_coverage * 0.25), 4)
        if status != QueryPlanStatus.VALID:
            confidence = min(confidence, 0.49)

        return ValidatedQueryPlan(
            status=status,
            plan_hash=plan_hash,
            confidence=confidence,
            evidence_ids=sorted(set(draft.evidence_ids)),
            errors=[*errors, *(f"AMBIGUITY:{code}" for code in sorted(set(ambiguity_codes)))],
            plan=draft,
        )
