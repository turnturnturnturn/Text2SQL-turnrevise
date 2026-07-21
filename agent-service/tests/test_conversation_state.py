from __future__ import annotations

from types import SimpleNamespace

from app.context_v2 import ConversationStateCompactor


def message(role: str, content: str, message_id: str, **metadata):
    return SimpleNamespace(
        role=role,
        content=content,
        metadata={"message_id": message_id, **metadata},
    )


def test_compactor_keeps_structured_constraints_and_message_provenance():
    state = ConversationStateCompactor().compact(
        "conversation-1",
        [
            message("user", "默认使用 created_at 统计销售时间", "m1"),
            message("assistant", "收到", "m2"),
            message("user", "后来改用 paid_at，并按华东区域统计", "m3"),
            message(
                "tool",
                "raw row: customer phone 13812345678",
                "m4",
                artifact_id="artifact:result-1",
                result_summary="华东共 12 条",
            ),
        ],
        previous_version=2,
    )

    assert state is not None
    assert state.summary_version == 3
    assert state.source_message_ids == ("m1", "m2", "m3", "m4")
    assert any("paid_at" in item.value for item in state.confirmed_constraints)
    assert not any("created_at" in item.value for item in state.confirmed_constraints)
    assert state.result_refs[0].artifact_id == "artifact:result-1"
    assert state.result_refs[0].summary == "华东共 12 条"
    assert "13812345678" not in str(state)


def test_compactor_tracks_open_question_decision_and_rejected_option():
    state = ConversationStateCompactor().compact(
        "conversation-2",
        [
            message("assistant", "销售时间用 paid_at 还是 created_at？", "m1"),
            message(
                "user",
                "选择 paid_at，不要 created_at",
                "m2",
                decision_id="column:orders.paid_at",
                evidence_ids=["column:orders.paid_at"],
            ),
        ],
    )

    assert state is not None
    assert state.open_questions == ()
    assert state.decisions[0].asset_id == "column:orders.paid_at"
    assert "created_at" in state.rejected_options[0].value


def test_compaction_failure_returns_none_for_recent_turn_fallback():
    def broken(_messages):
        raise RuntimeError("extractor unavailable")

    assert ConversationStateCompactor(extractor=broken).compact(
        "conversation-3", [message("user", "hello", "m1")]
    ) is None

