"""Real OpenAI check of grounded summaries. Opt-in only: OPENAI_API_KEY and JIDAN_RUN_OPENAI=1.

Writing operations run at reasoning effort medium (the per-operation routing is configured
elsewhere; here the provider is built with it directly).
"""

import json
import os
import re
import uuid

import pytest

from app.ai import DEFAULT_MODEL
from app.ai.contracts import DialogueTurn, IntentBrief, IntentSummaryRequest
from app.ai.openai_provider import OpenAiProvider
from app.ai.retrieval import Utterance, chunk_utterances, retrieve

pytestmark = pytest.mark.openai

INTENT = IntentBrief(
    key="COMMON_TASKS", stage="COMMON_TASKS", base_question="마감할 때 어떤 일을 하나요?",
    coverage_criteria="마감 작업의 순서, 각 작업의 완료 기준",
)
QUESTION = INTENT.base_question
ANSWER = ("마감 30분 전에 커피 머신 세척 버튼을 눌러요. 세척이 끝나면 드립 트레이를 비워요. "
          "그다음 쓰레기를 분리수거해서 뒷문 밖에 내놔요. 포스 정산은 사장님이 직접 하세요.")
PROBE = "쓰레기를 내놓은 뒤 확인할 것이 있나요?"
PROBE_ANSWER = "뒷문 잠금을 두 번 확인하면 끝이에요."


class CapturingProvider(OpenAiProvider):
    """Keeps the raw candidates so the citations (stripped from the result) can be inspected."""

    raw: list[str]

    def _complete(self, *args):
        self.raw = super()._complete(*args)
        return self.raw


def test_live_summary_cites_only_given_evidence_and_adds_no_facts():
    turns = [Utterance(str(uuid.uuid4()), "WORK_STRUCTURE", "오픈조는 9시부터 15시까지 일해요.", "근무조가 있나요?"),
             Utterance(str(uuid.uuid4()), "COMMON_TASKS", ANSWER, QUESTION),
             Utterance(str(uuid.uuid4()), "COMMON_TASKS", PROBE_ANSWER, PROBE)]
    evidence = retrieve(chunk_utterances(turns), f"{INTENT.base_question}\n{INTENT.coverage_criteria}",
                        required_intent="COMMON_TASKS")
    provider = CapturingProvider(
        api_key=os.environ["OPENAI_API_KEY"], model=os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
        transcribe_model="gpt-transcribe", reasoning_effort="medium", timeout_seconds=180.0)
    request = IntentSummaryRequest(
        intent=INTENT, needs_detail=False, evidence=evidence,
        dialogue=(DialogueTurn(question=QUESTION, answer=ANSWER, depth=0),
                  DialogueTurn(question=PROBE, answer=PROBE_ANSWER, depth=1)))
    result = provider.summarize_intent(request)

    raw = next(json.loads(c) for c in provider.raw if c.strip().startswith("{"))
    allowed = {chunk.id for chunk in evidence}
    raw_steps = [step for section in raw["structure"]["sections"] for step in section["steps"]]
    assert raw_steps, "the owner described closing steps"
    for step in raw_steps:  # every step cites real evidence
        assert step["evidence_ids"] and set(step["evidence_ids"]) <= allowed, step
    # Nothing was dropped by grounding, so the stored manual is exactly what the model wrote.
    assert sum(len(s.steps) for s in result.structure.sections) == len(raw_steps)
    # Another intent's evidence is context only: a common-task summary defines no shifts.
    assert result.structure.shifts == ()
    # No invented facts: every number in a step was said by the owner.
    spoken = " ".join(chunk.text for chunk in evidence)
    for step in raw_steps:
        assert set(re.findall(r"\d+", step["instruction"])) <= set(re.findall(r"\d+", spoken)), step
    print("\n[live grounding]", json.dumps({
        "evidence": [(c.id[-6:], c.intent_key, c.text) for c in evidence],
        "steps": [(s["instruction"], [i[-6:] for i in s["evidence_ids"]]) for s in raw_steps],
        "shifts": raw["structure"]["shifts"], "missing": raw["structure"]["missing_information"],
    }, ensure_ascii=False, indent=1))
