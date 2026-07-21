from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

import psycopg
from psycopg.rows import dict_row

from app.rollout.models import FEATURES, RolloutDecision, RolloutPolicy


_decision: ContextVar[RolloutDecision | None] = ContextVar("rollout_decision", default=None)


@contextmanager
def bind_rollout_decision(decision: RolloutDecision):
    token = _decision.set(decision)
    try:
        yield
    finally:
        _decision.reset(token)


def current_rollout_decision() -> RolloutDecision | None:
    return _decision.get()


def effective_mode(feature: str, default: str) -> str:
    decision = _decision.get()
    return decision.modes.get(feature, default) if decision else default


class InMemoryRolloutStore:
    def __init__(self, policies=None) -> None:
        self.policies = list(policies or [])
        self.decisions: list[dict] = []
        self._lock = asyncio.Lock()
        self._monitor_versions: set[tuple[str, int]] = set()

    async def list_policies(self, *, active_only=False):
        values = [p for p in self.policies if p.active or not active_only]
        return deepcopy(values)

    async def add_policy(self, policy: RolloutPolicy):
        async with self._lock:
            self.policies.append(policy)
        return policy

    async def persist_decision(self, run_id: str, decision: RolloutDecision):
        async with self._lock:
            self.decisions.append({"run_id": run_id, "decision": deepcopy(decision)})

    async def claim_monitor_version(self, policy_key: str, version: int) -> bool:
        async with self._lock:
            key = (policy_key, version)
            if key in self._monitor_versions:
                return False
            self._monitor_versions.add(key)
            return True


class PostgresRolloutStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(self.database_url, row_factory=dict_row)

    @staticmethod
    def _policy(row) -> RolloutPolicy:
        return RolloutPolicy(
            policy_id=str(row["id"]), policy_key=row["policy_key"],
            version=row["version"], scope_type=row["scope_type"],
            scope_value=row["scope_value"], cohort_percent=row["cohort_percent"],
            modes=dict(row["modes"]), active=row["active"],
            downgrade_reason=row["downgrade_reason"], created_by=row["created_by"],
            created_at=row["created_at"],
        )

    async def list_policies(self, *, active_only=False):
        condition = "WHERE active" if active_only else ""
        async with await self._connect() as conn:
            rows = await (await conn.execute(
                f"SELECT * FROM agent_state.rollout_policies {condition} ORDER BY version DESC"
            )).fetchall()
        return [self._policy(row) for row in rows]

    async def add_policy(self, policy: RolloutPolicy):
        async with await self._connect() as conn:
            row = await (await conn.execute(
                """INSERT INTO agent_state.rollout_policies
                   (id,policy_key,version,scope_type,scope_value,cohort_percent,modes,
                    active,downgrade_reason,created_by,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) RETURNING *""",
                (policy.policy_id, policy.policy_key, policy.version, policy.scope_type,
                 policy.scope_value, policy.cohort_percent, json.dumps(policy.modes),
                 policy.active, policy.downgrade_reason, policy.created_by, policy.created_at)
            )).fetchone()
        return self._policy(row)

    async def persist_decision(self, run_id: str, decision: RolloutDecision):
        async with await self._connect() as conn:
            await conn.execute(
                """INSERT INTO agent_state.rollout_decisions
                   (run_id,policy_id,policy_version,tenant_id,cohort,bucket,modes,reason)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (run_id) DO NOTHING""",
                (run_id, decision.policy_id, decision.policy_version, decision.tenant_id,
                 decision.cohort, decision.bucket, json.dumps(decision.modes), decision.reason)
            )

    async def claim_monitor_version(self, policy_key: str, version: int) -> bool:
        lock_key = f"phase-d-monitor:{policy_key}:{version}"
        async with await self._connect() as conn:
            row = await (await conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
                (lock_key,),
            )).fetchone()
            return bool(row["locked"])

    async def load_signals(self):
        from app.rollout.monitor import RolloutSignals
        async with await self._connect() as conn:
            row = await (await conn.execute(
                """SELECT
                   count(*) FILTER (WHERE event_type IN ('run_completed','run_failed','run_cancelled')
                     AND created_at >= now()-interval '15 minutes') AS samples,
                   count(*) FILTER (WHERE event_type='run_failed'
                     AND created_at >= now()-interval '15 minutes') AS errors,
                   count(*) FILTER (WHERE attributes->>'db_copilot.safety_execution'='true'
                     AND created_at >= now()-interval '15 minutes') AS safety_executions,
                   count(*) FILTER (WHERE attributes->>'db_copilot.cross_user_leak'='true'
                     AND created_at >= now()-interval '15 minutes') AS cross_user_leaks,
                   count(*) FILTER (WHERE attributes->>'db_copilot.provenance_missing'='true'
                     AND created_at >= now()-interval '15 minutes') AS provenance_missing,
                   count(*) FILTER (WHERE event_type IN ('run_completed','run_failed','run_cancelled')
                     AND created_at < now()-interval '15 minutes'
                     AND created_at >= now()-interval '30 minutes') AS baseline_samples,
                   count(*) FILTER (WHERE event_type='run_failed'
                     AND created_at < now()-interval '15 minutes'
                     AND created_at >= now()-interval '30 minutes') AS baseline_errors,
                   percentile_cont(.95) WITHIN GROUP (
                     ORDER BY NULLIF(attributes->>'duration_ms','')::double precision / 1000
                   ) FILTER (WHERE event_type='run_completed'
                     AND COALESCE(attributes->>'db_copilot.route_class','simple')='simple'
                     AND created_at >= now()-interval '30 minutes') AS simple_p95,
                   percentile_cont(.95) WITHIN GROUP (
                     ORDER BY NULLIF(attributes->>'duration_ms','')::double precision / 1000
                   ) FILTER (WHERE event_type='run_completed'
                     AND attributes->>'db_copilot.route_class'='complex'
                     AND created_at >= now()-interval '30 minutes') AS complex_p95
                   FROM agent_state.run_trace_events"""
            )).fetchone()
            affected_policy = await (await conn.execute(
                """SELECT attributes->>'db_copilot.rollout_policy_key' AS policy_key,
                          count(*) AS failures
                   FROM agent_state.run_trace_events
                   WHERE event_type='run_failed'
                     AND created_at >= now()-interval '15 minutes'
                     AND attributes ? 'db_copilot.rollout_policy_key'
                   GROUP BY policy_key ORDER BY failures DESC,policy_key LIMIT 1"""
            )).fetchone()
            affected_route = await (await conn.execute(
                """SELECT concat(COALESCE(attributes->>'route','chat'),':',
                                  COALESCE(attributes->>'risk_level','LOW')) AS route_risk,
                          max(NULLIF(attributes->>'duration_ms','')::double precision) AS duration
                   FROM agent_state.run_trace_events
                   WHERE event_type='run_completed'
                     AND created_at >= now()-interval '30 minutes'
                   GROUP BY route_risk ORDER BY duration DESC NULLS LAST LIMIT 1"""
            )).fetchone()
            closure = await (await conn.execute(
                """SELECT count(*) FILTER (WHERE status IN ('COMPLETED','FAILED','CANCELLED','NEEDS_CLARIFICATION')) AS closed,
                          count(*) AS total
                   FROM agent_state.agent_runs
                   WHERE started_at >= now()-interval '30 minutes'
                     AND started_at < now()-interval '5 minutes'"""
            )).fetchone()
        baseline_rate = (
            row["baseline_errors"] / row["baseline_samples"]
            if row["baseline_samples"] else 0.0
        )
        closure_rate = closure["closed"] / closure["total"] if closure["total"] else 1.0
        return RolloutSignals(
            safety_executions=row["safety_executions"],
            cross_user_leaks=row["cross_user_leaks"],
            error_count=row["errors"], sample_count=row["samples"],
            baseline_error_rate=baseline_rate,
            simple_p95_seconds=float(row["simple_p95"] or 0),
            complex_p95_seconds=float(row["complex_p95"] or 0),
            p95_breach_minutes=30,
            provenance_missing=row["provenance_missing"],
            non_terminal_closure_rate=closure_rate,
            affected_policy_key=(affected_policy or {}).get("policy_key"),
            affected_route_risk=(affected_route or {}).get("route_risk"),
        )


class RolloutPolicyService:
    PRIORITY = {"user": 0, "tenant": 1, "role": 2, "route_risk": 3, "global": 4}

    def __init__(self, store) -> None:
        self.store = store

    async def decide(self, *, user_id: str, tenant_id: str, role: str, route: str, risk: str):
        policies = await self.store.list_policies(active_only=True)
        matches = []
        expected = {
            "user": user_id, "tenant": tenant_id, "role": role,
            "route_risk": f"{route}:{risk}", "global": "*",
        }
        for item in policies:
            if item.scope_value == expected[item.scope_type]:
                matches.append(item)
        if not matches:
            raise RuntimeError("no active rollout policy")
        selected = min(matches, key=lambda item: (self.PRIORITY[item.scope_type], -item.version))
        digest = hashlib.sha256(f"{selected.version}:{user_id}".encode()).digest()
        bucket = int.from_bytes(digest, "big") % 100
        enabled = bucket < selected.cohort_percent
        modes = {
            name: (mode if enabled or mode == "off" else "shadow")
            for name, mode in selected.modes.items()
        }
        return RolloutDecision(
            policy_id=selected.policy_id, policy_version=selected.version,
            policy_key=selected.policy_key, tenant_id=tenant_id,
            cohort="enabled" if enabled else "control", bucket=bucket,
            modes=modes, reason=f"matched:{selected.scope_type}",
        )

    async def persist(self, run_id: str, decision: RolloutDecision):
        await self.store.persist_decision(run_id, decision)

    async def list_policies(self):
        return await self.store.list_policies(active_only=False)

    async def rollback(self, policy_id: str, *, actor: str, reason: str):
        policies = await self.store.list_policies(active_only=False)
        target = next((item for item in policies if item.policy_id == policy_id), None)
        if target is None:
            raise KeyError(policy_id)
        version = max(item.version for item in policies if item.policy_key == target.policy_key) + 1
        rolled = RolloutPolicy(
            policy_id=str(uuid.uuid4()), policy_key=target.policy_key, version=version,
            scope_type=target.scope_type, scope_value=target.scope_value,
            cohort_percent=target.cohort_percent,
            modes={name: "shadow" for name in FEATURES},
            created_by=actor, downgrade_reason=reason,
        )
        return await self.store.add_policy(rolled)
