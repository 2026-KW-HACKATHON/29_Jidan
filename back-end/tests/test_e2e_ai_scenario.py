"""The demo E2E's AI side (e2e.ai_scenario): deterministic fake answers, live routing and its call budget."""
import json

import pytest

from app.ai.contracts import (
    DialogueTurn,
    IntentBrief,
    IntentSummaryRequest,
    QaRequest,
    SectionItem,
    StepItem,
    StructureRevisionRequest,
    StructureSnapshot,
    SufficiencyRequest,
    TranscriptionRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider
from e2e import ai_scenario


def intent(key: str) -> IntentBrief:
    return IntentBrief(key=key, stage="COMMON_TASKS", base_question="무엇을 하나요?", coverage_criteria="순서")


TURN = DialogueTurn(question="무엇을 하나요?", answer="포스 마감을 해요.", depth=0)


@pytest.fixture
def fake():
    return ai_scenario.install(FakeAiProvider())


def judged(fake, key, depth):
    # A real dialogue at `depth` has one answered question per depth (Jev sees turns, not the counter).
    dialogue = tuple(DialogueTurn(question="무엇을 하나요?", answer="포스 마감을 해요.", depth=d) for d in range(depth + 1))
    return fake.judge_sufficiency(SufficiencyRequest(intent=intent(key), dialogue=dialogue, depth=depth)).sufficient


def test_sufficiency_probes_common_tasks_once_and_equipment_to_depth_five(fake):
    assert [judged(fake, "COMMON_TASKS", depth) for depth in (0, 1)] == [False, True]
    assert [judged(fake, "EQUIPMENT", depth) for depth in range(6)] == [False] * 6
    assert all(judged(fake, key, 0) for key in ("WORK_STRUCTURE", "SHIFT_TASKS", "RULES", "EXCEPTIONS"))


@pytest.mark.parametrize("key", ["WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "RULES", "EQUIPMENT", "EXCEPTIONS"])
def test_every_intent_summary_passes_server_validation(fake, key):
    summary = fake.summarize_intent(IntentSummaryRequest(intent=intent(key), dialogue=(TURN,), needs_detail=False))
    assert summary.structure.shifts or summary.structure.sections
    if key == "COMMON_TASKS":
        [section] = summary.structure.sections
        assert section.title == ai_scenario.POS_SECTION and len(section.steps) == 2


def manual(*sections: SectionItem) -> StructureSnapshot:
    return StructureSnapshot(sections=sections)


POS = SectionItem(id="s-pos", category="COMMON_TASK", title=ai_scenario.POS_SECTION,
                  steps=(StepItem(id="t-1", instruction="정산을 눌러요."), StepItem(id="t-2", instruction="금고에 넣어요.")))
OTHER = SectionItem(id="s-other", category="RULE", title="규칙", steps=(StepItem(id="t-3", instruction="앞치마"),))


def test_pos_questions_cite_the_pos_section_and_others_need_the_owner(fake):
    answered = fake.answer_question(QaRequest(question="포스 마감은요?", manual=manual(OTHER, POS)))
    assert answered.outcome == "ANSWERED" and answered.text == ai_scenario.POS_ANSWER
    [citation] = answered.citations
    assert (citation.section_id, citation.step_ids) == ("s-pos", ("t-1", "t-2"))
    other = fake.answer_question(QaRequest(question="주차는요?", manual=manual(OTHER, POS)))
    assert (other.outcome, other.citations) == ("NEEDS_OWNER", ())


def test_revision_rewrites_the_last_step_or_moves_the_first_shift(fake):
    revised = fake.revise_structure(StructureRevisionRequest(current=manual(POS), instruction="사진을 보내요.",
                                                             require_manual_level=False))
    assert [s.instruction for s in revised.structure.sections[0].steps] == ["정산을 눌러요.", "사진을 보내요."]
    assert revised.summary is None
    shifts = StructureSnapshot.model_validate({"shifts": [{"id": "h-1", "name": "오픈조", "start_time": "09:00",
                                                           "end_time": "15:00", "ends_next_day": False}]})
    moved = fake.revise_structure(StructureRevisionRequest(current=shifts, summary="요약", instruction="8시 30분"))
    assert moved.structure.shifts[0].start_time == ai_scenario.SHIFT_08_30 and moved.summary == "8시 30분"


class Recorder(FakeAiProvider):
    provider_name, model = "openai", "gpt-6-luna"


def routed(tmp_path, limit=2, ops=("answer_question",)):
    counter = tmp_path / "calls.json"
    live = ai_scenario.install(Recorder())
    return ai_scenario.RoutedAiProvider(ai_scenario.install(FakeAiProvider()), live, ops,
                                        ai_scenario.CallBudget(limit, str(counter))), live, counter


def test_routing_sends_only_live_ops_to_the_live_provider(tmp_path):
    provider, live, counter = routed(tmp_path)
    provider.judge_sufficiency(SufficiencyRequest(intent=intent("RULES"), dialogue=(TURN,), depth=0))
    provider.answer_question(QaRequest(question="포스?", manual=manual(POS)))
    assert [call.operation for call in live.calls] == ["answer_question"]
    assert json.loads(counter.read_text()) == {"limit": 2, "calls": {"answer_question": 1}, "refused": 0}


def test_budget_refuses_past_the_limit_without_retry(tmp_path):
    provider, live, counter = routed(tmp_path, limit=1, ops=("transcribe",))
    request = TranscriptionRequest(audio=b"x", mime_type="audio/wav")
    assert provider.transcribe(request).text == "테스트 전사 결과예요."
    with pytest.raises(AiError) as refused:
        provider.transcribe(request)
    assert refused.value.code == AiErrorCode.NOT_CONFIGURED and not refused.value.retryable
    assert len(live.calls) == 1  # the refused call never reached the live provider
    assert json.loads(counter.read_text()) == {"limit": 1, "calls": {"transcribe": 1}, "refused": 1}


def test_unknown_live_operation_is_refused(tmp_path):
    with pytest.raises(ValueError):
        routed(tmp_path, ops=("judge", "answer_question"))


def test_default_live_ops_fit_the_default_budget():
    # summarize x6, compose, revise x2 (review + draft correction), answer x4 (M11, M12 x2, M13),
    # transcribe x2 (M3 recording, M12 question).
    assert set(ai_scenario.DEFAULT_LIVE_OPS) <= set(ai_scenario.OPERATIONS)
    assert 6 + 1 + 2 + 4 + 2 <= 20


def test_build_from_env(monkeypatch):
    monkeypatch.delenv("E2E_AI", raising=False)
    assert ai_scenario.build_from_env() is None
    monkeypatch.setenv("E2E_AI", "fake")
    assert isinstance(ai_scenario.build_from_env(), FakeAiProvider)
    monkeypatch.setenv("E2E_AI", "other")
    with pytest.raises(ValueError):
        ai_scenario.build_from_env()


def test_router_delegates_every_ai_operation():
    """A new operation must be routed too; otherwise the base class calls the router's
    `_complete`, which raises (write_section_from_media was missing at first)."""
    from app.ai.fake import OPERATIONS
    from app.ai.provider import AiProvider

    for operation in OPERATIONS:
        assert getattr(ai_scenario.RoutedAiProvider, operation) is not getattr(AiProvider, operation), operation
