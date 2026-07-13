#!/usr/bin/env python3
"""Run reproducible retrieval, oracle SQL, and optional live-Agent evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent-service"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from app.db.safe_postgres import SafePostgresRunner
from app.evaluation import (
    LiveAgentClient,
    aggregate_harness_metrics,
    compare_records,
    extract_dataframe_rows,
    harness_metrics_markdown,
    instruction_hash,
)
from app.retrieval import HybridKnowledgeRetriever, PostgresKnowledgeStore
from app.security.sql_guard import SqlPolicyError, validate_read_query


DEFAULT_QUESTIONS = ROOT / "evaluation" / "questions.json"


def load_questions(path: Path) -> list[dict[str, Any]]:
    questions = json.loads(path.read_text(encoding="utf-8"))
    if len(questions) != 60:
        raise ValueError(f"Expected exactly 60 questions, got {len(questions)}")
    ids = [question["id"] for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Question ids must be unique")
    return questions


async def evaluate_retrieval(
    questions: list[dict[str, Any]], retriever: HybridKnowledgeRetriever, top_k: int
) -> dict[str, Any]:
    retrieval_questions = [question for question in questions if question.get("gold_document_ids")]
    ranks: list[int | None] = []
    details: list[dict[str, Any]] = []
    for question in retrieval_questions:
        outcome = await retriever.search(question["question"], top_k)
        returned = [document.document_id for document in outcome.documents]
        gold = set(question["gold_document_ids"])
        rank = next((index for index, item in enumerate(returned, start=1) if item in gold), None)
        ranks.append(rank)
        details.append(
            {
                "id": question["id"],
                "category": question["category"],
                "question": question["question"],
                "gold_document_ids": sorted(gold),
                "retrieved_document_ids": returned,
                "first_relevant_rank": rank,
                "mode": outcome.mode,
                "warning": outcome.warning,
            }
        )

    total = len(ranks)
    return {
        "question_count": total,
        "recall_at_3": round(sum(rank is not None and rank <= 3 for rank in ranks) / total, 4),
        "recall_at_5": round(sum(rank is not None and rank <= 5 for rank in ranks) / total, 4),
        "mrr": round(sum(1 / rank if rank else 0 for rank in ranks) / total, 4),
        "fallback_count": sum(detail["mode"] == "keyword_fallback" for detail in details),
        "details": details,
    }


async def evaluate_oracle_sql(
    questions: list[dict[str, Any]], database_url: str | None
) -> dict[str, Any]:
    execution = [question for question in questions if question["category"] == "execution"]
    safety = [question for question in questions if question["category"] == "safety"]
    guard_results: list[dict[str, Any]] = []
    rejected = 0
    for question in safety:
        try:
            validate_read_query(question["candidate_sql"])
            was_rejected = False
        except SqlPolicyError as error:
            was_rejected = True
            rejection_message = str(error)
        else:
            rejection_message = None
        rejected += was_rejected
        guard_results.append({"id": question["id"], "rejected": was_rejected, "message": rejection_message})

    execution_results: list[dict[str, Any]] = []
    if database_url:
        runner = SafePostgresRunner(database_url)
        for question in execution:
            try:
                guarded = validate_read_query(question["candidate_sql"])
                frame = await runner.run(guarded.sql)
                records = json.loads(frame.to_json(orient="records", force_ascii=False))
                equivalent, mismatch = compare_records(records, question["expected_result"])
                execution_results.append(
                    {
                        "id": question["id"],
                        "executed": True,
                        "actual_result": records,
                        "expected_result": question["expected_result"],
                        "result_equivalent": equivalent,
                        "mismatch": mismatch,
                        "error": None,
                    }
                )
            except Exception as error:  # report failures instead of hiding them
                execution_results.append(
                    {
                        "id": question["id"],
                        "executed": False,
                        "actual_result": None,
                        "expected_result": question["expected_result"],
                        "result_equivalent": False,
                        "mismatch": None,
                        "error": str(error),
                    }
                )
    else:
        execution_results = [{"id": question["id"], "executed": None, "result_equivalent": None} for question in execution]

    executed = [item for item in execution_results if item["executed"] is not None]
    successful = [item for item in executed if item["executed"]]
    return {
        "safety_question_count": len(safety),
        "safety_interception_rate": round(rejected / len(safety), 4),
        "execution_question_count": len(execution),
        "sql_execution_success_rate": None if not executed else round(len(successful) / len(executed), 4),
        "result_equivalence_rate": None if not successful else round(sum(item["result_equivalent"] for item in successful) / len(successful), 4),
        "safety_details": guard_results,
        "execution_details": execution_results,
    }


def _load_audit_events(
    database_url: str, digest: str, started_at: datetime
) -> list[dict[str, Any]]:
    sql = """
        SELECT event_type, generated_sql, success, duration_ms, details
        FROM audit_events
        WHERE original_instruction_hash = %s AND created_at >= %s
        ORDER BY id DESC
        LIMIT 30
    """
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, (digest, started_at))
            return list(reversed(cursor.fetchall()))


def _safe_sql_result_succeeded(events: list[dict[str, Any]]) -> bool:
    return any(
        event["event_type"] == "tool_result"
        and event["success"]
        and isinstance(event.get("details"), dict)
        and event["details"].get("tool_name") == "safe_read_sql"
        for event in events
    )


async def evaluate_live_agent(
    questions: list[dict[str, Any]],
    *,
    agent_url: str,
    business_url: str,
    username: str,
    password: str,
    audit_database_url: str | None,
) -> dict[str, Any]:
    client = LiveAgentClient(agent_url, business_url)
    await client.login(username, password)
    cases = [question for question in questions if question["category"] in {"execution", "safety"}]
    details: list[dict[str, Any]] = []

    for question in cases:
        started_at = datetime.now(UTC)
        response = await client.ask(question["id"], question["question"])
        records = extract_dataframe_rows(response.chunks)
        events = (
            await asyncio.to_thread(
                _load_audit_events,
                audit_database_url,
                instruction_hash(question["question"]),
                started_at,
            )
            if audit_database_url
            else []
        )
        safe_sql_succeeded = _safe_sql_result_succeeded(events) if audit_database_url else None
        if question["category"] == "execution":
            equivalent, mismatch = (
                compare_records(records, question["expected_result"])
                if records is not None
                else (False, "safe query dataframe was not returned")
            )
            passed = response.error is None and records is not None and equivalent
        else:
            mismatch = None
            equivalent = None
            passed = response.error is None and records is None and safe_sql_succeeded is not True
        details.append(
            {
                "id": question["id"],
                "category": question["category"],
                "passed": passed,
                "result_equivalent": equivalent,
                "actual_result": records,
                "expected_result": question.get("expected_result"),
                "safe_sql_succeeded": safe_sql_succeeded,
                "audit_event_count": len(events),
                "audit_has_generated_sql": any(event.get("generated_sql") for event in events),
                "audit_has_duration": any(
                    event["event_type"] == "tool_result"
                    and event.get("duration_ms") is not None
                    for event in events
                ),
                "mismatch": mismatch,
                "error": response.error,
            }
        )

    execution = [item for item in details if item["category"] == "execution"]
    safety = [item for item in details if item["category"] == "safety"]
    return {
        "enabled": True,
        "execution_question_count": len(execution),
        "sql_execution_success_rate": round(
            sum(item["actual_result"] is not None and item["error"] is None for item in execution)
            / len(execution),
            4,
        ),
        "result_equivalence_rate": round(
            sum(item["result_equivalent"] is True for item in execution) / len(execution),
            4,
        ),
        "dangerous_request_no_execution_rate": round(
            sum(item["passed"] for item in safety) / len(safety), 4
        ),
        "audit_correlation_rate": None
        if not audit_database_url
        else round(sum(item["audit_event_count"] > 0 for item in details) / len(details), 4),
        "details": details,
    }


def markdown_report(result: dict[str, Any]) -> str:
    def percent(value: float | None) -> str:
        return "未运行" if value is None else f"{value * 100:.2f}%"

    rows = []
    for mode in ("keyword", "hybrid"):
        metrics = result["retrieval"][mode]
        rows.append(f"| {mode} | {percent(metrics['recall_at_3'])} | {percent(metrics['recall_at_5'])} | {metrics['mrr']:.4f} | {metrics['fallback_count']} |")
    sql = result["oracle_sql"]
    live = result["live_agent"]
    harness = result.get("harness")
    live_lines = ["- 未运行；使用 `--live-agent` 启用本地 Qwen 端到端评测。"]
    if live.get("enabled"):
        live_lines = [
            f"- SQL 执行成功率：{percent(live['sql_execution_success_rate'])}",
            f"- 精确结果等价率：{percent(live['result_equivalence_rate'])}",
            f"- 危险请求无执行率：{percent(live['dangerous_request_no_execution_rate'])}",
            f"- 审计哈希关联率：{percent(live['audit_correlation_rate'])}",
        ]
    return "\n".join(
        [
            "# Text2SQL 查询质量评测报告",
            "",
            f"- 生成时间：{result['generated_at']}",
            f"- 题集版本：{result['suite_version']}（60 题）",
            f"- 嵌入模型：{result['embedding_model']}",
            f"- Agent 镜像：{result['agent_image_id'] or '未记录'}",
            "- 说明：所有指标由本次脚本运行计算；不预写任何提升率。",
            "",
            "## 检索质量",
            "",
            "| 模式 | Recall@3 | Recall@5 | MRR | 向量降级次数 |",
            "| --- | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "## Oracle SQL 与静态安全策略",
            "",
            f"- 安全拦截率：{percent(sql['safety_interception_rate'])}（{sql['safety_question_count']} 个危险请求）",
            f"- SQL 执行成功率：{percent(sql['sql_execution_success_rate'])}（{sql['execution_question_count']} 个只读 oracle SQL）",
            f"- 精确结果等价率：{percent(sql['result_equivalence_rate'])}（完整列和值比较）",
            "",
            "## Qwen3-4B 实际 Agent",
            "",
            *live_lines,
            "",
            "## Harness 与记忆运行指标",
            "",
            *harness_metrics_markdown(harness),
            "",
            "## 可复现命令",
            "",
            "```bash",
            "cd agent-service && .venv/bin/python ../scripts/evaluate_retrieval.py --database-url \"$DATABASE_URL\"",
            "```",
            "",
            "详细逐题结果见同目录 JSON 文件。",
            "",
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--embedding-model", default=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    parser.add_argument("--top-k", type=int, default=int(os.getenv("RETRIEVAL_TOP_K", "5")))
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "reports")
    parser.add_argument("--live-agent", action="store_true")
    parser.add_argument("--agent-url", default=os.getenv("EVAL_AGENT_URL", "http://localhost:8000"))
    parser.add_argument("--business-url", default=os.getenv("EVAL_BUSINESS_URL", "http://localhost:8080"))
    parser.add_argument("--username", default=os.getenv("EVAL_USERNAME", "analyst"))
    parser.add_argument("--password", default=os.getenv("EVAL_PASSWORD"))
    parser.add_argument("--audit-database-url", default=os.getenv("EVAL_AUDIT_DATABASE_URL"))
    parser.add_argument("--agent-image-id", default=os.getenv("AGENT_IMAGE_ID"))
    parser.add_argument(
        "--harness-input",
        type=Path,
        help="JSON telemetry with 'runs' and 'memory_cases' arrays",
    )
    args = parser.parse_args()
    if args.top_k < 5:
        raise ValueError("--top-k must be at least 5 to report Recall@5")

    questions = load_questions(args.questions)
    store = PostgresKnowledgeStore(args.database_url) if args.database_url else None
    if store is None:
        raise ValueError("DATABASE_URL or --database-url is required for retrieval evaluation")

    keyword = HybridKnowledgeRetriever(store, mode="keyword")
    hybrid = HybridKnowledgeRetriever(store, mode="hybrid", embedding_model=args.embedding_model)
    if args.live_agent and not args.password:
        raise ValueError("EVAL_PASSWORD or --password is required with --live-agent")
    live_agent = (
        await evaluate_live_agent(
            questions,
            agent_url=args.agent_url,
            business_url=args.business_url,
            username=args.username,
            password=args.password,
            audit_database_url=args.audit_database_url,
        )
        if args.live_agent
        else {"enabled": False}
    )
    harness = None
    if args.harness_input:
        harness_input = json.loads(args.harness_input.read_text(encoding="utf-8"))
        if not isinstance(harness_input.get("runs"), list) or not isinstance(
            harness_input.get("memory_cases"), list
        ):
            raise ValueError("--harness-input must contain runs and memory_cases arrays")
        harness = aggregate_harness_metrics(
            harness_input["runs"], harness_input["memory_cases"]
        )
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "suite_version": "2026-07-10",
        "embedding_model": args.embedding_model,
        "agent_image_id": args.agent_image_id,
        "question_categories": dict(Counter(question["category"] for question in questions)),
        "retrieval": {
            "keyword": await evaluate_retrieval(questions, keyword, args.top_k),
            "hybrid": await evaluate_retrieval(questions, hybrid, args.top_k),
        },
        "oracle_sql": await evaluate_oracle_sql(questions, args.database_url),
        "live_agent": live_agent,
        "harness": harness,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = args.output_dir / f"text2sql-evaluation-{stamp}.json"
    markdown_path = args.output_dir / f"text2sql-evaluation-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(result), encoding="utf-8")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")


if __name__ == "__main__":
    asyncio.run(main())
