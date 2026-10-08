"""Test helpers for the owner AI interview (#120). Kept apart from tests/factories.py while the
AI features are built in parallel (team brief §10)."""

import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import InterviewIntent, InterviewQuestionSet
from app.interview.question_set import (
    CURRENT_QUESTION_SET_ID,
    CURRENT_REVISION_NO,
    INTENTS_V1,
    intent_id,
)
from app.tasks import drain


def ensure_question_set(db: Session) -> list[InterviewIntent]:
    """The seeded question set v1 (migration 0040). MySQL tests empty every table between
    tests, so this puts the same rows back when they are gone."""
    if db.get(InterviewQuestionSet, CURRENT_QUESTION_SET_ID) is None:
        db.add(InterviewQuestionSet(id=CURRENT_QUESTION_SET_ID, revision_no=CURRENT_REVISION_NO))
        db.flush()
        db.add_all([
            InterviewIntent(id=intent_id(CURRENT_REVISION_NO, d.key), question_set_id=CURRENT_QUESTION_SET_ID,
                            sort_order=order, intent_key=d.key, stage=d.stage, base_question=d.base_question,
                            coverage_criteria=d.coverage_criteria)
            for order, d in enumerate(INTENTS_V1)
        ])
        db.flush()
    return [db.get(InterviewIntent, intent_id(CURRENT_REVISION_NO, d.key)) for d in INTENTS_V1]


def raw_shift(ref, name, start="09:00", end="18:00", next_day=False):
    return {"ref": ref, "name": name, "start_time": start, "end_time": end, "ends_next_day": next_day}


def raw_section(ref, title, *, category="COMMON_TASK", shift_ref=None, steps=()):
    """`steps`: (ref, instruction) pairs; new refs are `new-<n>` placeholders."""
    return {"ref": ref, "category": category, "shift_ref": shift_ref, "title": title,
            "steps": [{"ref": step_ref, "instruction": text, "checklist_item": False}
                      for step_ref, text in steps]}


def raw_summary(summary="정리한 내용이에요.", shifts=(), sections=(), missing=()):
    return {"summary": summary, "structure": {"shifts": list(shifts), "sections": list(sections),
                                              "missing_information": list(missing)}}


# --- API driver ----------------------------------------------------------------------------------


class InterviewDriver:
    """Calls the interview endpoints as the store owner and runs queued AI tasks in-thread."""

    def __init__(self, api, auth, engine, store_id):
        self.api, self.auth, self.engine, self.store = api, auth, engine, store_id

    def url(self, session_id=None, *parts, store=None):
        base = f"/api/stores/{store or self.store}/manual/interviews"
        return "/".join([base, *([session_id] if session_id else []), *parts])

    def post(self, url, body, key=None, auth=None):
        return self.api.post(url, json=body, headers=(auth or self.auth).headers(key or str(uuid.uuid4())))

    def start(self, key=None, store=None, auth=None):
        return self.post(self.url(store=store), {}, key, auth)

    def run(self, rounds=4):
        """Run every due task, then (simulated) later again so backed-off retries run too."""
        runs = []
        for index in range(rounds):
            runs.extend(drain(now=utcnow() + timedelta(minutes=index * 2)))
        return runs

    def get(self, session_id):
        response = self.api.get(self.url(session_id))
        assert response.status_code == 200, response.text
        return response.json()

    def started(self):
        response = self.start()
        assert response.status_code == 201, response.text
        self.run()
        return response.json()["id"]

    def answer(self, session_id, text="네, 그렇게 해요.", *, question=None, revision=None, key=None,
               photo_ids=None, data=None):
        if question is None or revision is None:
            state = self.get(session_id)
            question = question or (state["questions"][0]["id"] if state["questions"] else str(uuid.uuid4()))
            revision = revision or state["revision"]
        body = {"expectedRevision": revision, "questionId": question,
                "input": data or {"method": "TEXT", "text": text}}
        if photo_ids is not None:
            body["photoIds"] = photo_ids
        return self.post(self.url(session_id, "answers"), body, key)

    def answer_and_run(self, session_id, text="네, 그렇게 해요."):
        response = self.answer(session_id, text)
        assert response.status_code == 202, response.text
        self.run()
        return self.get(session_id)

    def reviews(self, session_id):
        response = self.api.get(self.url(session_id, "reviews"))
        assert response.status_code == 200, response.text
        return response.json()

    def review_url(self, session_id, intent_id, *parts):
        return self.url(session_id, "intents", intent_id, "review", *parts)

    def review(self, session_id, intent_id):
        return self.api.get(self.review_url(session_id, intent_id))

    def complete(self, session_id, revision=None, items=None, key=None):
        listing = self.reviews(session_id)
        body = {"expectedRevision": revision or listing["sessionRevision"],
                "reviewRevisions": items if items is not None else [
                    {"intentId": i["intentId"], "revision": i["revision"]} for i in listing["items"]]}
        return self.post(self.url(session_id, "completion"), body, key)

    def finish_all(self, session_id, intents=6):
        state = None
        for _ in range(intents):
            state = self.answer_and_run(session_id)
        return state
