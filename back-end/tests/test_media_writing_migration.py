"""0043 preserves a populated FK graph, generated columns and SQLite enforcement both ways."""
import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    InterviewReviewConfirmation,
    InterviewTurn,
    InterviewTurnPhoto,
    ManualDraftCorrection,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    MediaTranscription,
)
from tests.conftest import alembic_config
from tests.factories import NOW, make_interview, make_question_set, make_store
from tests.manual_factories import make_photo, make_ready_draft, sample_content

MODELS = (BackgroundTask, InterviewIntentReview, InterviewReviewConfirmation, InterviewTurn,
          InterviewTurnPhoto, ManualDraftCorrection, ManualMedia, ManualMediaSnapshotRef,
          ManualPhotoAttachment, MediaTranscription)


@pytest.fixture(params=["explicit", "legacy"])
def migration_engine(request, engine):
    if request.param == "explicit":
        yield engine
        return
    legacy = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(legacy, "connect")
    def foreign_keys(dbapi, _record):
        dbapi.execute("PRAGMA foreign_keys = ON")

    with legacy.connect() as connection:
        command.upgrade(alembic_config(connection), "head")
        connection.commit()
    try:
        yield legacy
    finally:
        legacy.dispose()


def seed_graph(engine):
    with Session(engine) as db:
        store = make_store(db)
        photo = make_photo(db, store)
        draft = make_ready_draft(db, store, sample_content(photo=photo.id))
        questions, intents = make_question_set(db)
        interview = make_interview(db, draft, questions, intents)
        review = InterviewIntentReview(session_id=interview.id, intent_id=intents[0].id,
                                       status="READY", ready_content={"summary": "기존 내용"})
        db.add(review)
        db.flush()
        db.add(InterviewReviewConfirmation(session_id=interview.id, intent_id=intents[0].id,
            reviewed_revision=1, confirmed_revision=2, confirmed_content=review.ready_content,
            owner_id=store.owner_id))
        turn = InterviewTurn(session_id=interview.id, intent_id=intents[0].id, turn_no=1,
                             speaker="AI", turn_kind="QUESTION", question_kind="BASE", content="어떻게 하나요?")
        db.add(turn)
        db.flush()
        db.add(InterviewTurnPhoto(turn_id=turn.id, media_id=photo.id, sort_order=0))
        db.add(ManualMediaSnapshotRef(media_id=photo.id, holder_kind="INTENT_REVIEW",
                                     holder_id=interview.id, holder_intent_id=intents[0].id))
        audio = make_photo(db, store, kind="AUDIO")
        task = BackgroundTask(kind="TRANSCRIPTION", subject_id=audio.id, payload={},
                              status="SUCCEEDED", tries=1, max_tries=3, finished_at=NOW)
        db.add(task)
        db.flush()
        transcript = MediaTranscription(store_id=store.id, manual_media_id=audio.id,
            status="READY", text="기존 전사", completed_at=NOW, task_id=task.id)
        db.add(transcript)
        db.flush()
        db.add(ManualDraftCorrection(version_id=draft.id, task_id=str(uuid.uuid4()), base_revision=1,
            target_kind="MANUAL", input_method="VOICE", input_text="기존 전사", transcription_id=transcript.id,
            status="SUCCEEDED", result_revision=1, completed_at=NOW, requested_by_owner_id=store.owner_id))
        db.commit()


def snapshot(connection):
    return {model.__tablename__: connection.execute(select(model.__table__).order_by(
        *model.__table__.primary_key.columns)).all() for model in MODELS}


@pytest.mark.parametrize("deferred", [0, 1])
def test_0043_roundtrip_preserves_existing_media_and_all_references(migration_engine, deferred):
    engine = migration_engine
    seed_graph(engine)
    with engine.connect() as connection:
        before = snapshot(connection)
        connection.commit()
        config = alembic_config(connection)
        for revision, migrate in (("0042", command.downgrade), ("head", command.upgrade)):
            if deferred:
                if not connection.in_transaction():
                    connection.begin()
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql(f"PRAGMA defer_foreign_keys = {deferred}")
            migrate(config, revision)
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA defer_foreign_keys").scalar() == deferred
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert snapshot(connection) == before
            connection.commit()  # DROP/restore must not leave deferred violations at COMMIT
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0042" if revision == "0042" else "0043")
            connection.rollback()


@pytest.mark.parametrize("deferred", [0, 1])
def test_0043_mid_rebuild_failure_rolls_back_and_can_be_retried(migration_engine, deferred):
    engine = migration_engine
    seed_graph(engine)
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0042")
        connection.commit()
        before = snapshot(connection)
        schema = connection.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").all()
        connection.commit()

        def fail_second_rebuild(_conn, _cursor, statement, _params, _context, _many):
            if statement.startswith("ALTER TABLE _0043_interview_intent_reviews RENAME"):
                raise RuntimeError("injected DDL failure")

        event.listen(engine, "before_cursor_execute", fail_second_rebuild)
        try:
            if deferred:
                if not connection.in_transaction():
                    connection.begin()
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql(f"PRAGMA defer_foreign_keys = {deferred}")
            with pytest.raises(RuntimeError, match="injected DDL failure"):
                command.upgrade(config, "head")
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA defer_foreign_keys").scalar() == deferred
            connection.rollback()
        finally:
            event.remove(engine, "before_cursor_execute", fail_second_rebuild)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert snapshot(connection) == before
        assert connection.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").all() == schema
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0042"
        connection.rollback()
        command.upgrade(config, "head")
        connection.commit()
        assert snapshot(connection) == before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0043"
