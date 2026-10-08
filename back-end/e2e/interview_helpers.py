"""Interview HTTP fixtures: only owner identity/store setup bypasses public APIs.

Requests use the real app.main server and its background runner. AI_PROVIDER=fake is
an external-provider boundary; no handler/dependency is replaced. real_db validates the
local host, dedicated MySQL database, migration head and production app routes first.
"""
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.db.models import InterviewTurn
from e2e.conftest import RegistrationCase
from tests.factories import NOW, make_store, make_user
from tests.interview_factories import ensure_question_set


@dataclass
class InterviewCase:
    client: httpx.Client
    engine: object
    store_id: str
    owner_id: str

    def url(self, session_id=None, suffix=""):
        root = f"/api/stores/{self.store_id}/manual/interviews"
        return root + (f"/{session_id}" if session_id else "") + suffix

    def post(self, url, body, *, key=None):
        headers = RegistrationCase(self.client, "", "").headers(key=key)
        return self.client.post(url, json=body, headers=headers)

    def start(self):
        response = self.post(self.url(), {})
        assert response.status_code == 201, response.text
        return response.json()

    def get(self, session_id):
        response = self.client.get(self.url(session_id))
        assert response.status_code == 200, response.text
        return response.json()

    def wait(self, session_id, predicate, *, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            state = self.get(session_id)
            if predicate(state):
                return state
            assert state["status"] != "ERROR", state
            assert time.monotonic() < deadline, state
            time.sleep(0.1)

    def answer(self, state, text="오픈조는 오전 9시부터 오후 3시까지 일해요.", *, key=None):
        return self.post(self.url(state["id"], "/answers"), {
            "expectedRevision": state["revision"], "questionId": state["questions"][0]["id"],
            "input": {"method": "TEXT", "text": text},
        }, key=key)

    def turns(self, session_id):
        with Session(self.engine) as db:
            return list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.session_id == session_id).order_by(InterviewTurn.turn_no)))


@contextmanager
def interview_case(real_db, base_url):
    with Session(real_db) as db:
        # Schema tests clear seeded rows; restore the unchanged migration0040 question set.
        ensure_question_set(db)
        identity = f"interview-http-{uuid.uuid4()}"
        owner = make_user(db, "OWNER", google_sub=identity, google_email=f"{identity}@e2e.test")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW,
                           business_registration_number=f"{uuid.uuid4().int % 10**10:010d}")
        issued = auth.create_session(owner.id, db=db)
        db.commit()
        owner_id, store_id = owner.id, store.id
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False,
                      headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={issued.token}"}) as client:
        yield InterviewCase(client, real_db, store_id, owner_id)
