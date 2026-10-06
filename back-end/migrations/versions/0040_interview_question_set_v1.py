"""Seed the required interview question set version 1 (#120).

Revision ID: 0040
Revises: 0036

Frozen copy of app.interview.question_set at this point (tests/test_interview_question_set.py
checks they agree). Never edit it after release; a new version is a new revision.
"""
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0036"
branch_labels = None
depends_on = None

NAMESPACE = uuid.UUID("5f0c6a52-8d0e-4c1b-9a51-2b7f1f6d4e10")
REVISION_NO = 1
SET_ID = str(uuid.uuid5(NAMESPACE, f"jidan:interview-question-set:{REVISION_NO}"))

INTENTS = (
    ("WORK_STRUCTURE", "WORK_STRUCTURE",
     "매장의 근무조는 어떻게 나뉘고, 각 근무조는 몇 시부터 몇 시까지 일하나요?",
     ("근무조의 이름과 개수, 각 근무조의 시작 시각과 종료 시각, 자정을 넘겨 다음 날 끝나는지 여부. "
      "근무조를 나누지 않고 한 가지 근무만 있다면 그 사실과 근무 시간.")),
    ("COMMON_TASKS", "COMMON_TASKS",
     "근무조와 상관없이 모든 직원이 공통으로 하는 일은 무엇인가요?",
     ("모든 근무조가 공통으로 하는 업무 각각의 작업 순서와 방법, 끝났다고 판단하는 기준. "
      "공통 업무가 없다고 분명히 말하면 그 사실.")),
    ("SHIFT_TASKS", "SHIFT_TASKS",
     "특정 근무조만 따로 맡아서 하는 일이 있다면 어떤 일인가요?",
     ("근무조별 업무와 그 업무를 맡는 근무조, 작업 순서와 방법, 끝났다고 판단하는 기준. "
      "근무조별로 따로 하는 일이 없다고 분명히 말하면 그 사실.")),
    ("RULES", "COMPLEMENTS",
     "직원이 일할 때 꼭 지켜야 하는 매장 규칙이 있나요?",
     ("점주가 정한 규칙마다 무엇을 지켜야 하는지와 적용되는 상황. "
      "따로 정한 규칙이 없다고 분명히 말하면 그 사실.")),
    ("EQUIPMENT", "COMPLEMENTS",
     "직원이 다루는 기계나 설비가 있다면 어떻게 사용하고 관리하나요?",
     ("점주가 말한 설비마다 사용 순서, 청소·관리 방법, 주의할 점. "
      "직원이 다루는 설비가 없다고 분명히 말하면 그 사실.")),
    ("EXCEPTIONS", "COMPLEMENTS",
     "평소와 다른 상황이 생기면 직원이 어떻게 대응하면 되나요?",
     ("점주가 말한 예외 상황마다 직원의 대응 방법과 점주에게 연락해야 하는 기준. "
      "따로 정한 대응이 없다고 분명히 말하면 그 사실.")),
)

question_sets = sa.table(
    "interview_question_sets",
    sa.column("id", sa.CHAR(36)), sa.column("revision_no", sa.Integer), sa.column("created_at", sa.DateTime),
)
intents = sa.table(
    "interview_intents",
    sa.column("id", sa.CHAR(36)), sa.column("question_set_id", sa.CHAR(36)),
    sa.column("sort_order", sa.Integer), sa.column("intent_key", sa.String(100)),
    sa.column("stage", sa.String(16)), sa.column("base_question", sa.Text),
    sa.column("coverage_criteria", sa.Text),
)


def upgrade() -> None:
    created = datetime(2026, 10, 6, tzinfo=UTC).replace(tzinfo=None)  # naive UTC DATETIME(6)
    op.bulk_insert(question_sets, [{"id": SET_ID, "revision_no": REVISION_NO, "created_at": created}])
    op.bulk_insert(intents, [
        {"id": str(uuid.uuid5(NAMESPACE, f"jidan:interview-intent:{REVISION_NO}:{key}")),
         "question_set_id": SET_ID, "sort_order": order, "intent_key": key, "stage": stage,
         "base_question": question, "coverage_criteria": criteria}
        for order, (key, stage, question, criteria) in enumerate(INTENTS)
    ])


def downgrade() -> None:
    # Fails on purpose while interviews still reference the set (FK): their history needs it.
    op.execute(intents.delete().where(intents.c.question_set_id == SET_ID))
    op.execute(question_sets.delete().where(question_sets.c.id == SET_ID))
