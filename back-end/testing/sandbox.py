"""Explicit local-only wrapper. app.main never imports or registers these routes."""
import os
import uuid
from datetime import date, time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import auth
from app.csrf import require_allowed_origin
from app.db import SessionDep
from app.db.models import AvailabilityDay, AvailabilityRule, User, WorkerProfile


def validate_environment() -> None:
    if (os.getenv("APP_ENV") != "local" or os.getenv("DB_NAME") != "jidan_sandbox"
            or os.getenv("DB_HOST") != "mysql" or os.getenv("COOKIE_SECURE") != "false"):
        raise RuntimeError("Sandbox requires the dedicated local Compose database")


def install_tools(app: FastAPI) -> None:
    @app.get("/sandbox", response_class=HTMLResponse, include_in_schema=False)
    def page():
        return HTMLResponse(Path(__file__).with_name("sandbox.html").read_text(),
                            headers={"Cache-Control": "no-store"})

    @app.post("/sandbox/login/{role}", dependencies=[Depends(require_allowed_origin)])
    def login(role: Literal["worker", "owner"], db: SessionDep):
        subject = f"sandbox-{role}"
        for attempt in range(3):
            try:
                user = db.scalar(select(User).where(User.google_sub == subject).with_for_update())
                if user is None:
                    user = User(google_sub=subject, google_email=f"{role}@sandbox.test", email_verified=True,
                                role=role.upper(), status="ACTIVE", name=f"테스트 {role}",
                                phone_number="01012345678")
                    db.add(user)
                    db.flush()
                    if role == "worker":
                        db.add(WorkerProfile(user_id=user.id, birth_date=date(2001, 3, 14),
                                             gender="FEMALE", experience_level="NEW"))
                        db.flush()
                        rule = AvailabilityRule(worker_id=user.id, sort_order=0, start_time=time(9),
                                                end_time=time(14), ends_next_day=False)
                        db.add(rule)
                        db.flush()
                        db.add(AvailabilityDay(rule_id=rule.id, weekday="MON"))
                issued = auth.create_session(user.id, db=db)
                db.commit()
                break
            except (IntegrityError, OperationalError) as error:
                db.rollback()
                code = error.orig.args[0] if error.orig.args else None
                # Concurrent first fixture logins can deadlock or race on the unique subject.
                if code not in {1062, 1205, 1213} or attempt == 2:
                    raise
        response = Response(status_code=204)
        auth.clear_registration_cookie(response)
        auth.set_session_cookie(response, issued)
        return response

    @app.post("/sandbox/registration", dependencies=[Depends(require_allowed_origin)])
    def registration(db: SessionDep):
        subject = f"sandbox-new-{uuid.uuid4()}"
        issued = auth.create_registration_session(subject, f"{subject}@sandbox.test", db=db)
        db.commit()
        response = Response(status_code=204)
        auth.clear_session_cookie(response)
        auth.set_registration_cookie(response, issued)
        return response


def create_app() -> FastAPI:
    validate_environment()  # reject before importing or mutating the real application
    from app.main import app

    install_tools(app)
    return app
