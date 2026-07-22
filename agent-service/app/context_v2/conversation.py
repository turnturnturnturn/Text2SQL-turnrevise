from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class StateValue:
    key: str
    value: str
    source_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateDecision:
    asset_id: str
    value: str
    evidence_ids: tuple[str, ...]
    source_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResultReference:
    artifact_id: str
    summary: str
    content_hash: str
    source_message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConversationState:
    conversation_id: str
    confirmed_constraints: tuple[StateValue, ...] = ()
    open_questions: tuple[StateValue, ...] = ()
    decisions: tuple[StateDecision, ...] = ()
    rejected_options: tuple[StateValue, ...] = ()
    result_refs: tuple[ResultReference, ...] = ()
    summary_version: int = 1
    source_message_ids: tuple[str, ...] = ()
    model_id: str = "deterministic-extractive-v1"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Extractor = Callable[[Sequence[Any]], dict[str, Any]]


def _metadata(message: Any) -> dict[str, Any]:
    value = getattr(message, "metadata", None)
    return value if isinstance(value, dict) else {}


def _message_id(message: Any, index: int) -> str:
    return str(_metadata(message).get("message_id") or f"message:{index}")


def _constraint_key(content: str) -> str:
    lowered = content.lower()
    if "paid_at" in lowered or "created_at" in lowered or "时间" in content:
        return "time_field"
    if "区域" in content or "region" in lowered:
        return "region"
    if "排序" in content or "sort" in lowered:
        return "sort"
    if "格式" in content or "表格" in content:
        return "format"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _extract(messages: Sequence[Any]) -> dict[str, Any]:
    constraints: dict[str, StateValue] = {}
    questions: list[StateValue] = []
    decisions: list[StateDecision] = []
    rejected: list[StateValue] = []
    results: list[ResultReference] = []

    for index, message in enumerate(messages):
        content = str(getattr(message, "content", "") or "").strip()
        role = str(getattr(message, "role", ""))
        metadata = _metadata(message)
        message_id = _message_id(message, index)
        if role == "tool" or metadata.get("kind") == "tool_output":
            artifact_id = metadata.get("artifact_id")
            summary = metadata.get("result_summary")
            if artifact_id and summary:
                results.append(
                    ResultReference(
                        artifact_id=str(artifact_id),
                        summary=str(summary),
                        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        source_message_ids=(message_id,),
                    )
                )
            continue
        if not content:
            continue
        if role == "assistant" and (content.endswith(("?", "？")) or "还是" in content):
            questions.append(
                StateValue("open_question", content, (message_id,))
            )
        if role == "user" and re.search(r"默认|使用|改用|按|选择|确认", content):
            constraints[_constraint_key(content)] = StateValue(
                _constraint_key(content), content, (message_id,)
            )
        decision_id = metadata.get("decision_id")
        if role == "user" and decision_id:
            decisions.append(
                StateDecision(
                    asset_id=str(decision_id),
                    value=content,
                    evidence_ids=tuple(map(str, metadata.get("evidence_ids", []))),
                    source_message_ids=(message_id,),
                )
            )
        rejected_match = re.search(r"(?:不要|排除|不使用)\s*([^,，。]+)", content)
        if rejected_match:
            rejected.append(
                StateValue("rejected_option", rejected_match.group(1).strip(), (message_id,))
            )

    if decisions:
        questions = []
    return {
        "confirmed_constraints": tuple(constraints.values()),
        "open_questions": tuple(questions),
        "decisions": tuple(decisions),
        "rejected_options": tuple(rejected),
        "result_refs": tuple(results),
    }


class ConversationStateCompactor:
    def __init__(self, *, extractor: Extractor = _extract, model_id: str = "deterministic-extractive-v1"):
        self.extractor = extractor
        self.model_id = model_id

    def compact(
        self,
        conversation_id: str,
        messages: Sequence[Any],
        *,
        previous_version: int = 0,
    ) -> ConversationState | None:
        try:
            extracted = self.extractor(messages)
        except Exception:
            return None
        source_ids = tuple(_message_id(message, index) for index, message in enumerate(messages))
        return ConversationState(
            conversation_id=conversation_id,
            summary_version=previous_version + 1,
            source_message_ids=source_ids,
            model_id=self.model_id,
            **extracted,
        )

