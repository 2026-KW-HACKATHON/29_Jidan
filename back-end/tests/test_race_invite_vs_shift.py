"""Invitation acceptance (#108, link and inbox) racing a work request acceptance (#114).

Both create a store access grant for the same worker and store: the invitation side locks the
store row first, the work request side locks the posting and worker rows. Through the real
endpoints on MySQL they must neither deadlock nor block each other's grant: REGULAR and
TEMPORARY access coexist independently.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ShiftAssignment, StoreAccessGrant
from tests.invitation_helpers import seed_invitation
from tests.jobs_support import client_factory, key, race
from tests.test_work_requests import clock as _clock
from tests.test_work_requests import scene as _scene

clock = _clock
scene = _scene
ROUNDS = 5


def _email(db_engine, worker_id):
    from app.db.models import User

    with Session(db_engine) as db:
        return db.get(User, worker_id).google_email


def _race_once(scene, channel):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    invitation_id, token = seed_invitation(scene.db_engine, scene.store_id, email=_email(scene.db_engine, worker))
    me = scene.as_worker(worker)

    def accept_shift(client):
        return client.post(f"/api/users/me/work-requests/{request['id']}/response",
                           json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))

    def accept_invitation(client):
        if channel == "link":
            return client.post("/api/store-invitations/accept", json={"token": token}, headers=me.headers())
        return client.post(f"/api/users/me/store-invitations/{invitation_id}/response",
                           json={"decision": "ACCEPT"}, headers=me.headers(str(uuid.uuid4())))

    make = client_factory(me.token)
    shift, invitation = race([(make, accept_shift), (make, accept_invitation)])
    assert shift.status_code == 200, shift.text
    assert invitation.status_code == 200, invitation.text
    assert invitation.json()["accessGrant"]["type"] == "REGULAR"
    with Session(scene.db_engine) as db:
        grants = db.scalars(select(StoreAccessGrant).where(
            StoreAccessGrant.store_id == scene.store_id, StoreAccessGrant.worker_id == worker)).all()
        assert sorted("REGULAR" if g.invitation_id else "TEMPORARY" for g in grants) == ["REGULAR", "TEMPORARY"]
        assert db.scalar(select(ShiftAssignment.id).where(ShiftAssignment.worker_id == worker)) is not None


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
@pytest.mark.parametrize("channel", ["link", "inbox"])
def test_invitation_and_work_request_acceptance_race(scene, channel):
    for _ in range(ROUNDS):
        _race_once(scene, channel)
