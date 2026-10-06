"""MEDIA_STORAGE_503: a storage write failure (disk full, I/O error) is 503 JOB_QUEUE_UNAVAILABLE
with everything rolled back, not a 500 (openapi uploadManualMedia). Programming errors stay 500."""

import errno
import os
import uuid

import pytest

from app.db.models import IdempotencyRecord, ManualMedia
from app.media import storage as storage_module
from app.media.storage import LocalMediaStorage
from tests import media_samples as samples
from tests.test_manual_media_api import (  # noqa: F401 (fixtures)
    code,
    count,
    ctx,
    media_root,
    upload,
)


def leftovers(storage: LocalMediaStorage) -> list[str]:
    """Every file under the media root, temporary uploads included."""
    return [os.path.join(top, name) for top, _dirs, names in os.walk(storage.root) for name in names]


class FullDisk(list):
    """fsync fails with ENOSPC while `full` is set; each failing call is recorded."""

    full = True


@pytest.fixture
def full_disk(monkeypatch):
    disk, real = FullDisk(), os.fsync

    def fsync(fd):
        if disk.full:
            disk.append(1)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd)

    monkeypatch.setattr(storage_module.os, "fsync", fsync)
    return disk


@pytest.mark.parametrize("purpose,data", [("MANUAL_PHOTO", samples.png()), ("INTERVIEW_AUDIO", samples.wav_seconds(1))],
                         ids=["photo", "audio"])
def test_storage_failure_is_503_rolled_back_and_retryable_with_the_same_key(ctx, monkeypatch, full_disk, purpose, data):  # noqa: F811
    key = str(uuid.uuid4())
    before = (count(ctx, ManualMedia), count(ctx, IdempotencyRecord))
    response = upload(ctx, data, purpose, key=key)
    assert (response.status_code, code(response)) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert full_disk == [1]
    assert (count(ctx, ManualMedia), count(ctx, IdempotencyRecord)) == before
    assert leftovers(ctx.storage) == []
    full_disk.full = False  # the disk has room again
    retried = upload(ctx, data, purpose, key=key)
    assert retried.status_code == 201, retried.text
    assert count(ctx, ManualMedia) == before[0] + 1
    assert len(list(ctx.storage.iter_files("manual"))) == 1 and len(leftovers(ctx.storage)) == 1
    replay = upload(ctx, data, purpose, key=key)
    assert replay.status_code == 201 and replay.headers.get("Idempotent-Replayed") == "true"
    assert count(ctx, ManualMedia) == before[0] + 1


def test_programming_errors_at_the_storage_boundary_stay_500(ctx, monkeypatch):  # noqa: F811
    from app import manual_media

    monkeypatch.setattr(manual_media, "object_key", lambda *_args: "../escape")  # InvalidObjectKey
    response = upload(ctx, samples.png())
    assert (response.status_code, code(response)) == (500, "INTERNAL_ERROR")
    assert count(ctx, ManualMedia) == 0 and leftovers(ctx.storage) == []


def test_validation_errors_are_not_turned_into_503(ctx, full_disk):  # noqa: F811
    response = upload(ctx, b"", "MANUAL_PHOTO")
    assert (response.status_code, code(response)) == (422, "MEDIA_INVALID")
    assert full_disk == []  # rejected before any write


def test_storage_write_reports_unavailability_and_removes_the_temporary_file(tmp_path, monkeypatch, full_disk):
    from app.media.storage import StorageUnavailable

    storage = LocalMediaStorage(tmp_path / "media")
    key = storage_module.object_key("manual", str(uuid.uuid4()), str(uuid.uuid4()))
    with pytest.raises(StorageUnavailable) as raised:
        storage.write(key, b"bytes")
    assert isinstance(raised.value.__cause__, OSError) and raised.value.__cause__.errno == errno.ENOSPC
    assert leftovers(storage) == []
    with pytest.raises(storage_module.InvalidObjectKey):
        storage.write("../escape", b"bytes")  # not an availability problem
