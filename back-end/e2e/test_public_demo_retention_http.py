"""Expired visitor cleanup after real HTTP writes; test DB and media only."""
import io
import os
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, BackgroundTask, ManualMedia, QaMedia, Store, User
from app.demo import SAMPLE_STORE
from e2e.test_public_demo_http import headers, start
from e2e.test_public_demo_http import public_demo as _public_demo

public_demo = _public_demo


def test_expired_cleanup_preserves_other_visitors_sample_and_removes_files(public_demo):
    clients, engine = public_demo
    owner, worker = clients["OWNER"], clients["WORKER"]
    owner_session, worker_session = start(owner), start(worker)
    interview = owner.post(f"/api/stores/{owner_session['storeId']}/manual/interviews", json={},
                           headers={**headers(owner, owner_session), "Idempotency-Key": str(uuid.uuid4())})
    assert interview.status_code == 201, interview.text
    image = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(image, format="PNG")
    media_path = f"/api/stores/{worker_session['storeId']}/manual/qa/media"
    upload = worker.post(media_path, data={"purpose": "QUESTION_IMAGE"},
                         files={"file": ("demo.png", image.getvalue(), "image/png")},
                         headers={**headers(worker, worker_session), "Idempotency-Key": str(uuid.uuid4())})
    assert upload.status_code == 201, upload.text
    media_id = upload.json()["id"]
    owner_upload = owner.post(f"/api/stores/{owner_session['storeId']}/manual/media",
                              data={"purpose": "MANUAL_PHOTO"},
                              files={"file": ("demo.png", image.getvalue(), "image/png")},
                              headers={**headers(owner, owner_session), "Idempotency-Key": str(uuid.uuid4())})
    assert owner_upload.status_code == 201, owner_upload.text
    owner_media_id = owner_upload.json()["id"]
    with httpx.Client(base_url=worker.base_url, timeout=10, trust_env=False) as other:
        other_session = start(other)
        assert other.get(media_path + f"/{media_id}/content").status_code == 404
        assert other.delete(media_path + f"/{media_id}",
                            headers={**headers(other, other_session), "Idempotency-Key": str(uuid.uuid4())}).status_code == 404
        assert worker.get(media_path + f"/{media_id}/content").status_code == 200
    with Session(engine) as db:
        retired = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            worker.cookies[auth.SESSION_COOKIE_NAME])))
        owner_row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            owner.cookies[auth.SESSION_COOKIE_NAME])))
        owner_id, worker_id = owner_row.user_id, retired.user_id
        auth.create_session(owner_id, db=db)  # a second valid session must preserve the user
        owner_file = Path(os.environ["MEDIA_ROOT"]) / db.get(ManualMedia, owner_media_id).object_key
        media = db.get(QaMedia, media_id)
        assert media.worker_id == worker_id
        file_path = Path(os.environ["MEDIA_ROOT"]) / media.object_key
        assert file_path.is_file()
        user = db.get(User, worker_id)
        user.created_at = utcnow() - timedelta(hours=3)
        retired.expires_at = utcnow() - timedelta(hours=1)
        # Old creation time alone must never delete a still-valid session.
        db.get(User, owner_id).created_at = utcnow() - timedelta(hours=3)
        db.commit()
    command = [sys.executable, "-m", "app.demo_retention"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    with Session(engine) as db:
        assert db.get(User, worker_id) is None
        assert db.get(QaMedia, media_id) is None
        assert db.get(User, owner_id) is not None
        assert db.get(Store, SAMPLE_STORE) is not None
        assert db.scalar(select(BackgroundTask.id).where(BackgroundTask.subject_id == interview.json()["id"]))
    assert not file_path.exists()
    assert owner.get(f"/api/stores/{owner_session['storeId']}/manual").status_code == 200
    assert worker.get(media_path + f"/{media_id}/content").status_code == 401
    # Expiring the owner now removes the queued AI input payload as well as its workspace.
    with Session(engine) as db:
        for row in db.scalars(select(AuthSession).where(AuthSession.user_id == owner_id)):
            row.expires_at = utcnow() - timedelta(hours=1)
        db.commit()
    failure = "from app import demo_retention; def_placeholder = None\n" \
              "def fail(*args): raise RuntimeError('injected cleanup failure')\n" \
              "demo_retention.delete_accounts = fail\ndemo_retention.purge()"
    result = subprocess.run([sys.executable, "-c", failure], capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode != 0
    with Session(engine) as db:
        assert db.get(User, owner_id) is not None
        assert db.get(ManualMedia, owner_media_id) is not None
        assert db.scalar(select(BackgroundTask.id).where(BackgroundTask.subject_id == interview.json()["id"]))
    assert owner_file.is_file()
    # Simulate a task's finalizing transaction committing a successor while cleanup waits
    # for the predecessor PK. This is the real MySQL/task enqueue boundary, not an API mock.
    with Session(engine) as writer:
        task = writer.scalar(select(BackgroundTask).where(
            BackgroundTask.subject_id == interview.json()["id"]).with_for_update())
        cleaner = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while True:
                with engine.connect() as connection:
                    queries = connection.execute(text("SHOW FULL PROCESSLIST")).mappings().all()
                if any(row["Info"] and row["Info"].upper().startswith("DELETE FROM BACKGROUND_TASKS") for row in queries):
                    break
                assert cleaner.poll() is None
                assert time.monotonic() < deadline
                time.sleep(.02)
            from app.tasks import enqueue
            enqueue(writer, task.kind, task.subject_id, task.payload, input_revision=task.input_revision)
            writer.commit()
            _, stderr = cleaner.communicate(timeout=30)
            assert cleaner.returncode == 0, stderr
        finally:
            writer.rollback()
            if cleaner.poll() is None:
                cleaner.kill()
                cleaner.wait(timeout=5)
    with Session(engine) as db:
        assert db.get(User, owner_id) is None
        assert db.get(Store, owner_session["storeId"]) is None
        assert db.scalar(select(BackgroundTask.id).where(BackgroundTask.subject_id == interview.json()["id"])) is None
        assert db.get(Store, SAMPLE_STORE) is not None
    assert not owner_file.exists()
    # Re-running after all expired visitors are gone is harmless.
    assert subprocess.run(command, capture_output=True, timeout=30, check=False).returncode == 0


@pytest.mark.parametrize("retired", ["expired", "revoked"])
def test_grace_boundary_and_two_concurrent_cleaners(public_demo, retired):
    clients, engine = public_demo
    owner = clients["OWNER"]
    start(owner)
    with Session(engine) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            owner.cookies[auth.SESSION_COOKIE_NAME])))
        user_id = row.user_id
        if retired == "revoked":
            row.revoked_at = utcnow()
            db.get(User, user_id).created_at = utcnow() - timedelta(hours=3)
        boundary = (row.revoked_at or row.expires_at) + timedelta(minutes=30)
        db.commit()
    code = "import sys; from datetime import datetime; from app.demo_retention import purge; purge(now=datetime.fromisoformat(sys.argv[1]))"
    command = [sys.executable, "-c", code]
    assert subprocess.run([*command, (boundary - timedelta(microseconds=1)).isoformat()],
                          capture_output=True, timeout=30, check=False).returncode == 0
    with Session(engine) as db:
        assert db.get(User, user_id) is not None
    processes = [subprocess.Popen([*command, boundary.isoformat()], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
    with Session(engine) as db:
        assert db.get(User, user_id) is None
        assert db.get(Store, SAMPLE_STORE) is not None
