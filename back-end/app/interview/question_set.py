"""The fixed required question set (version 1) every new interview uses.

Rows are seeded by migration 0040 with the same fixed UUIDs (uuid5) and texts; a new version
gets a new migration and a new constant here, and sessions keep the version they started with
(docs/erd/manual.md: a used question set is never edited).

Order follows the interview stages (openapi startManualInterview): 근무 구조 → 공통 업무 →
근무조별 업무 → 보완 사항(규칙·설비·예외). `coverage_criteria` is what Jev checks; "해당 없음"
answers count as covered, so a store without equipment is not probed five times.
"""

import uuid
from dataclasses import dataclass

NAMESPACE = uuid.UUID("5f0c6a52-8d0e-4c1b-9a51-2b7f1f6d4e10")


@dataclass(frozen=True)
class IntentDefinition:
    key: str
    stage: str
    base_question: str
    coverage_criteria: str


def question_set_id(revision_no: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"jidan:interview-question-set:{revision_no}"))


def intent_id(revision_no: int, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"jidan:interview-intent:{revision_no}:{key}"))


CURRENT_REVISION_NO = 1
CURRENT_QUESTION_SET_ID = question_set_id(CURRENT_REVISION_NO)

INTENTS_V1: tuple[IntentDefinition, ...] = (
    IntentDefinition(
        "WORK_STRUCTURE", "WORK_STRUCTURE",
        "매장의 근무조는 어떻게 나뉘고, 각 근무조는 몇 시부터 몇 시까지 일하나요?",
        "근무조의 이름과 개수, 각 근무조의 시작 시각과 종료 시각, 자정을 넘겨 다음 날 끝나는지 여부. "
        "근무조를 나누지 않고 한 가지 근무만 있다면 그 사실과 근무 시간.",
    ),
    IntentDefinition(
        "COMMON_TASKS", "COMMON_TASKS",
        "근무조와 상관없이 모든 직원이 공통으로 하는 일은 무엇인가요?",
        "모든 근무조가 공통으로 하는 업무 각각의 작업 순서와 방법, 끝났다고 판단하는 기준. "
        "공통 업무가 없다고 분명히 말하면 그 사실.",
    ),
    IntentDefinition(
        "SHIFT_TASKS", "SHIFT_TASKS",
        "특정 근무조만 따로 맡아서 하는 일이 있다면 어떤 일인가요?",
        "근무조별 업무와 그 업무를 맡는 근무조, 작업 순서와 방법, 끝났다고 판단하는 기준. "
        "근무조별로 따로 하는 일이 없다고 분명히 말하면 그 사실.",
    ),
    IntentDefinition(
        "RULES", "COMPLEMENTS",
        "직원이 일할 때 꼭 지켜야 하는 매장 규칙이 있나요?",
        "점주가 정한 규칙마다 무엇을 지켜야 하는지와 적용되는 상황. "
        "따로 정한 규칙이 없다고 분명히 말하면 그 사실.",
    ),
    IntentDefinition(
        "EQUIPMENT", "COMPLEMENTS",
        "직원이 다루는 기계나 설비가 있다면 어떻게 사용하고 관리하나요?",
        "점주가 말한 설비마다 사용 순서, 청소·관리 방법, 주의할 점. "
        "직원이 다루는 설비가 없다고 분명히 말하면 그 사실.",
    ),
    IntentDefinition(
        "EXCEPTIONS", "COMPLEMENTS",
        "평소와 다른 상황이 생기면 직원이 어떻게 대응하면 되나요?",
        "점주가 말한 예외 상황마다 직원의 대응 방법과 점주에게 연락해야 하는 기준. "
        "따로 정한 대응이 없다고 분명히 말하면 그 사실.",
    ),
)
