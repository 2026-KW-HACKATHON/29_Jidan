"""Local media storage: server-derived keys only, no traversal, atomic private files."""

import os
import stat
import time
import uuid

import pytest

from app.media.storage import InvalidObjectKey, LocalMediaStorage, object_key


@pytest.fixture
def storage(tmp_path):
    return LocalMediaStorage(tmp_path / "media")


def key(scope="manual"):
    return object_key(scope, str(uuid.uuid4()), str(uuid.uuid4()))


def test_round_trip_is_private_and_idempotent_to_delete(storage):
    name = key()
    storage.write(name, b"bytes")
    assert storage.read(name) == b"bytes" and storage.exists(name)
    path = storage.root / name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    storage.write(name, b"new")  # atomic replace
    assert storage.read(name) == b"new"
    assert storage.delete(name) is True and storage.delete(name) is False
    with pytest.raises(FileNotFoundError):
        storage.read(name)


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "manual/../../x", "/etc/passwd", "manual/abc/def",
    f"other/{uuid.uuid4()}/{uuid.uuid4()}", f"manual/{uuid.uuid4()}/{uuid.uuid4()}/x",
    f"manual/{str(uuid.uuid4()).upper()}/{uuid.uuid4()}", f"manual/{uuid.uuid4()}/../{uuid.uuid4()}",
    "", None,
])
def test_keys_other_than_server_generated_uuids_are_refused(storage, bad):
    with pytest.raises(InvalidObjectKey):
        storage.write(bad, b"x")
    with pytest.raises(InvalidObjectKey):
        storage.read(bad)


def test_object_key_builder_validates_parts():
    with pytest.raises(InvalidObjectKey):
        object_key("manual", "../x", str(uuid.uuid4()))
    with pytest.raises(InvalidObjectKey):
        object_key("public", str(uuid.uuid4()), str(uuid.uuid4()))


def test_symlinked_escape_is_refused(storage, tmp_path):
    store, media = str(uuid.uuid4()), str(uuid.uuid4())
    (storage.root / "manual").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, storage.root / "manual" / store)
    with pytest.raises(InvalidObjectKey):
        storage.write(f"manual/{store}/{media}", b"x")
    assert list(outside.iterdir()) == []


def test_listing_and_stale_temporaries(storage):
    names = {key(), key()}
    for name in names:
        storage.write(name, b"x")
    storage.write(key("qa"), b"y")
    assert {item.key for item in storage.iter_files("manual")} == names
    leftover = storage.root / next(iter(names)).rsplit("/", 1)[0] / ".upload-crashed"
    leftover.write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(leftover, (old, old))
    assert storage.remove_stale_temporaries() == 1 and not leftover.exists()
