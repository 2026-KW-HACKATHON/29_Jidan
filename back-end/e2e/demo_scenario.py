"""The #122 demo scenario over HTTP against a real server, every response checked against OpenAPI.

    cd back-end
    APP_ENV=local DB_HOST=... DB_NAME=jidan_local DB_USER=... DB_PASSWORD=... \
        python -m e2e.demo_scenario --start-server

Flow: owner sign-up -> admin approval -> worker sign-ups -> invitations (one accepted from the
inbox) -> posting -> applications -> work request and its withdrawal -> acceptance ->
confirmation withdrawal (reopen) -> acceptance again -> the other invitation accepted from the
link in the real e-mail by the confirmed worker (REGULAR and TEMPORARY access coexist) -> home and
calendar -> a work request left unanswered until its deadline -> notifications (exact types,
targets and counts per recipient, read and unreadCount) -> security checks. Prints PASS/FAIL per
step, with the `requestId` of a failed API call, and exits non-zero on any failure.

Google sign-in cannot be automated, so the runner starts registration sessions the way the
OAuth callback does (`create_registration_session`) and uses the real registration API after
that. It writes to the same database as the server (`DB_*`), guarded like the demo seed (never
production, DB name ending in _local/_dev/_demo/_test). The app has no test backdoor; every
business step is a real API call with Origin, CSRF token and Idempotency-Key.

Mail: the runner runs an SMTP server (e2e.mailbox). `--start-server` points the application at
it (`INVITATION_MAIL_BACKEND=smtp`, `SMTP_SECURITY=none`); against a server you started, set
those yourself with `SMTP_PORT` = `--smtp-port`. Invitation mail is delivered by the server's
own periodic job (every 60 s), so the scenario waits for it while other steps run.

Unanswered request: the scenario opens a posting that starts at a whole minute 65-125 s away, so
the request's deadline is the shift start (min(+1 h, start)), and waits for it in real time.
Then it checks the request reads EXPIRED, the owner closes the posting through the API, and the
owner has exactly one WORK_REQUEST_NO_RESPONSE: recorded by that closure (which materializes the
expiry) or by the server's own periodic sweep, whichever ran first -- the event key makes it one
row either way. No clock is faked and the runner calls no sweep itself. `--no-realtime-expiry`
leaves out that wait and its two steps (tests/test_e2e_demo.py does so unless JIDAN_E2E_FULL=1).

Cleanup: `python -m e2e.demo_scenario --cleanup` deletes every account the runner created
(Google `sub` starting with `e2e:`) and everything that belongs to them, with the same guards.
Demo seed accounts (`demo-seed:`) and real accounts are never matched.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from jsonschema import ValidationError

BACK_END = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACK_END))

from app import demo_seed
from app.auth import (
    REGISTRATION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_registration_session,
)
from app.db import session_scope, utcnow
from app.jobs import common
from app.operator_cli import operator_cli
from e2e import ai_scenario
from e2e.mailbox import Mailbox, text_of, token_from_link
from e2e.manual_qa_steps import QaSteps
from e2e.manual_scenario import ManualSteps
from tests.api_contract import validate_response

ADDRESS = {"postalCode": "01897", "address": "서울특별시 노원구 광운로 20", "detailAddress": "1층"}
OUTSIDE = {"postalCode": "01905", "address": "서울특별시 노원구 월계로 372"}
MAIL_WAIT_SECONDS = 90
SUB_PREFIX = "e2e:"  # Google `sub` of every account the runner creates
# Background notification types that depend on the wall clock rather than on this scenario.
CLOCK_TYPES = {"WORK_REMINDER"}


class StepFailed(Exception):
    def __init__(self, message: str, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class Actor:
    """One browser: its own cookie jar and CSRF token; every response is contract-checked."""

    def __init__(self, base_url: str, origin: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=30, follow_redirects=False)
        self.origin = origin
        self.csrf: str | None = None

    def refresh_csrf(self) -> None:
        self.csrf = self.call("GET", "/api/auth/csrf", expect=200).json()["csrfToken"]

    def call(self, method: str, path: str, *, expect: int, json=None, params=None, key: str | None = None,
             write: bool | None = None, headers: dict | None = None, files=None, data=None) -> httpx.Response:
        write = method != "GET" if write is None else write
        sent = {}
        if write:
            sent = {"Origin": self.origin, "X-CSRF-Token": self.csrf or ""}
            if key is not None:
                sent["Idempotency-Key"] = key
        sent.update(headers or {})
        response = self.http.request(method, path, json=json, params=params, headers=sent, files=files, data=data)
        try:
            validate_response(response)
        except (AssertionError, ValidationError) as error:
            raise StepFailed(f"contract: {method} {path} {response.status_code}: {str(error)[:300]}", response) from None
        if response.status_code != expect:
            raise StepFailed(f"{method} {path}: expected {expect}, got {response.status_code}", response)
        return response

    def error_code(self, response: httpx.Response) -> str:
        return response.json().get("code", "")


def check(condition: bool, message: str, response: httpx.Response | None = None) -> None:
    if not condition:
        raise StepFailed(message, response)


def key() -> str:
    return str(uuid.uuid4())


def seoul_text(value: str) -> str:
    """How the invitation mail writes a moment (app.invitation_mail.seoul_text)."""
    moment = datetime.fromisoformat(value).astimezone(common.SEOUL)
    return f"{moment.year}년 {moment.month}월 {moment.day}일 {moment:%H:%M} (한국 시간)"


class Scenario(ManualSteps, QaSteps):
    def __init__(self, base_url: str, origin: str, admin_password: str, fake_kakao: bool,
                 mailbox: Mailbox | None, realtime_expiry: bool = True, manual: bool = True,
                 ai: str = "fake") -> None:
        self.base_url, self.origin, self.admin_password = base_url, origin, admin_password
        self.fake_kakao, self.mailbox, self.realtime_expiry = fake_kakao, mailbox, realtime_expiry
        self.manual, self.live = manual, ai == "live"
        self.run = secrets.token_hex(4)
        self.state: dict = {}
        self.results: list[tuple[str, str, str]] = []
        self.expected: dict[str, Counter] = {}  # notifications per recipient, by type
        self.expected_targets: dict[str, Counter] = {}  # ... and by (type, target)

    def actor(self) -> Actor:
        return Actor(self.base_url, self.origin)

    # -- notifications -------------------------------------------------------------------------

    def notifications(self, name: str) -> list[dict]:
        body = self.state[name].call("GET", "/api/users/me/notifications", params={"read": "ALL", "size": 100},
                                     expect=200).json()
        return [item for item in body["items"] if item["type"] not in CLOCK_TYPES]

    def expect_notification(self, name: str, type_: str, target: dict) -> None:
        """Assert `name` now has exactly the expected notifications: counts by type and by
        (type, target), this new one unread. Two events may share a target (e.g. two invitations
        accepted at one store), so targets are counted, not required to be unique."""
        self.expected.setdefault(name, Counter())[type_] += 1
        targets = self.expected_targets.setdefault(name, Counter())
        targets[(type_, json.dumps(target, sort_keys=True))] += 1
        items = self.notifications(name)
        counts = Counter(item["type"] for item in items)
        check(counts == self.expected[name], f"{name} notifications {dict(counts)} != {dict(self.expected[name])}")
        seen = Counter((item["type"], json.dumps(item["target"], sort_keys=True)) for item in items)
        check(seen == targets, f"{name} notification targets {dict(seen)} != {dict(targets)}")
        newest = [item for item in items if item["type"] == type_ and item["target"] == target]
        check(any(item["readAt"] is None for item in newest), f"{name}'s new {type_} is unread")

    # -- sign-up -------------------------------------------------------------------------------

    def registration(self, role: str) -> tuple[Actor, str]:
        actor = self.actor()
        email = f"e2e-{self.run}-{role}@jidan.example"
        issued = create_registration_session(f"{SUB_PREFIX}{self.run}:{role}", email)
        actor.http.cookies.set(REGISTRATION_COOKIE_NAME, issued.token)
        context = actor.call("GET", "/api/auth/registration", expect=200).json()
        check(context["identity"]["email"] == email, "registration context shows the Google e-mail")
        actor.refresh_csrf()
        return actor, email

    def step_owner_signup(self):
        owner, email = self.registration("owner")
        body = {"name": "이점주", "phoneNumber": "01011112222", "store": {
            "name": f"E2E 카페 {self.run}", "industry": "CAFE", **ADDRESS,
            "businessRegistrationNumber": f"{int(self.run, 16) % 10**10:010d}", "phoneNumber": "029401234"}}
        if self.fake_kakao:
            outside = {**body, "store": {**body["store"], **OUTSIDE}}
            refused = owner.call("POST", "/api/auth/registrations/owners", json=outside, key=key(), expect=422)
            check(owner.error_code(refused) == "STORE_OUTSIDE_SERVICE_AREA", "outside 월계1동 is refused", refused)
        created = owner.call("POST", "/api/auth/registrations/owners", json=body, key=key(), expect=201).json()
        check(created["nextAction"] == "OWNER_APPROVAL_PENDING", "a new owner waits for approval")
        check(SESSION_COOKIE_NAME in owner.http.cookies, "the member session cookie was set")
        owner.refresh_csrf()
        store = created["user"]["stores"][0]
        self.state.update(owner=owner, owner_email=email, store_id=store["storeId"], store_name=body["store"]["name"])
        check(self.notifications("owner") == [], "a new owner has no notifications")

    def step_pending_owner_is_limited(self):
        owner, store_id = self.state["owner"], self.state["store_id"]
        home = owner.call("GET", "/api/owners/me/home", expect=200).json()
        check(home["stores"][0]["approvalStatus"] == "PENDING" and home["recruitingCount"] == 0, "pending home")
        refused = owner.call("POST", f"/api/stores/{store_id}/job-postings", json=self.job_body(2), key=key(),
                             expect=403)
        check(owner.error_code(refused) == "STORE_APPROVAL_REQUIRED", "pending store cannot post", refused)

    def step_admin_approval(self):
        admin = self.actor()
        foreign = admin.call("POST", "/api/admin/store-approval-requests/search", write=True,
                             json={"password": self.admin_password, "status": "PENDING"},
                             headers={"Origin": "https://evil.example"}, expect=403)
        check(admin.error_code(foreign) == "CSRF_INVALID", "an admin request from a foreign page is refused", foreign)
        wrong = admin.call("POST", "/api/admin/store-approval-requests/search", write=True,
                           json={"password": "wrong-" + self.admin_password, "status": "PENDING"}, expect=401)
        check(admin.error_code(wrong) == "ADMIN_PASSWORD_INVALID", "a wrong admin password is refused", wrong)
        found = admin.call("POST", "/api/admin/store-approval-requests/search", write=True,
                           json={"password": self.admin_password, "status": "PENDING", "size": 100}, expect=200).json()
        mine = [item for item in found["items"] if item["store"]["id"] == self.state["store_id"]]
        check(len(mine) == 1, "the new store is in the pending approval list")
        approved = admin.call("POST", f"/api/admin/store-approval-requests/{mine[0]['id']}/approve", write=True,
                              json={"password": self.admin_password}, expect=200).json()
        check(approved["status"] == "APPROVED", "the request is approved")
        session = self.state["owner"].call("GET", "/api/auth/session", expect=200).json()
        check(session["nextAction"] == "OWNER_HOME", "the owner's next screen is the owner home")
        self.expect_notification("owner", "STORE_APPROVED", {"type": "STORE", "storeId": self.state["store_id"]})

    def step_worker_signups(self):
        workers = (
            ("worker-a", "김근무", [{"days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                                    "startTime": "17:00", "endTime": "23:00", "endsNextDay": False}]),
            ("worker-b", "박지원", [{"days": ["SAT", "SUN"], "startTime": "09:00", "endTime": "13:00",
                                    "endsNextDay": False}]),
            ("worker-c", "최초대", [{"days": ["MON"], "startTime": "10:00", "endTime": "12:00",
                                    "endsNextDay": False}]),
        )
        for role, name, availability in workers:
            self.register_worker(role, name, availability)

    def register_worker(self, role: str, name: str, availability: list[dict]) -> None:
        worker, email = self.registration(role)
        body = {"name": name, "phoneNumber": "01033334444", "birthDate": "2001-05-05", "gender": "FEMALE",
                "experienceLevel": "EXPERIENCED",
                "careers": [{"industry": "CAFE", "duties": "음료 제조", "startMonth": "2024-01",
                             "endMonth": "2025-02", "isCurrent": False}],
                "availabilities": availability}
        created = worker.call("POST", "/api/auth/registrations/workers", json=body, key=key(), expect=201).json()
        check(created["nextAction"] == "WORKER_HOME", "a worker lands on the worker home")
        worker.refresh_csrf()
        self.state[role] = worker
        self.state[f"{role}-email"] = email
        self.state[f"{role}-id"] = created["user"]["id"]

    def step_invitations(self):
        owner, store_id = self.state["owner"], self.state["store_id"]
        for role in ("worker-a", "worker-c"):
            created = owner.call("POST", f"/api/stores/{store_id}/invitations", key=key(),
                                 json={"email": self.state[f"{role}-email"]}, expect=201).json()
            check(created["deliveryStatus"] == "QUEUED", "the invitation mail is queued")
            self.state[f"{role}-invitation"] = created["invitation"]
            # Both are registered workers with this verified e-mail, so each is notified.
            self.expect_notification(role, "STORE_INVITED",
                                     {"type": "STORE_INVITATION", "invitationId": created["invitation"]["id"]})

    def step_inbox_acceptance(self):
        owner, worker, store_id = self.state["owner"], self.state["worker-c"], self.state["store_id"]
        inbox = worker.call("GET", "/api/users/me/store-invitations", expect=200).json()
        mine = [item for item in inbox["items"] if item["store"]["id"] == store_id]
        check(len(mine) == 1, "worker C sees the invitation in the inbox")
        worker.call("POST", f"/api/users/me/store-invitations/{mine[0]['id']}/response", key=key(),
                    json={"decision": "ACCEPT"}, expect=200)
        stores = worker.call("GET", "/api/users/me/stores", expect=200).json()
        check(any(item["store"]["id"] == store_id for item in stores["items"]), "the store is now accessible")
        self.expect_notification("owner", "INVITATION_ACCEPTED", {"type": "STORE", "storeId": store_id})
        check(len(owner.call("GET", f"/api/stores/{store_id}/workers", expect=200).json()["items"]) == 1,
              "the owner sees one regular worker")

    def job_body(self, days: int, start: str = "18:00", end: str = "22:00", title: str = "E2E 저녁 대타",
                 work_date=None) -> dict:
        work_date = work_date or common.seoul_today(utcnow()) + timedelta(days=days)
        return {"title": title, "description": "홀 서빙과 마감 정리", "workPart": "OTHER",
                "workDate": work_date.isoformat(), "startTime": start, "endTime": end, "endsNextDay": end <= start,
                "minimumExperience": "MONTHS_3", "experienceNotes": "", "hourlyPay": 12000,
                "paymentTiming": "WORK_DAY", "paymentNotes": ""}

    def step_unanswered_request_setup(self):
        """A posting starting at a whole minute about two minutes away; its request expires then."""
        owner, store_id, worker = self.state["owner"], self.state["store_id"], self.state["worker-b"]
        start = (utcnow() + timedelta(seconds=125)).astimezone(common.SEOUL).replace(second=0, microsecond=0)
        end = start + timedelta(hours=1)
        body = self.job_body(0, f"{start:%H:%M}", f"{end:%H:%M}", "E2E 곧 시작 대타", start.date())
        job = owner.call("POST", f"/api/stores/{store_id}/job-postings", json=body, key=key(), expect=201).json()
        application = worker.call("POST", f"/api/job-postings/{job['id']}/applications", key=key(),
                                  json={"introduction": "바로 가능합니다."}, expect=201).json()
        self.expect_notification("owner", "NEW_APPLICATION", {
            "type": "JOB_APPLICATION", "applicationId": application["id"], "storeId": store_id, "jobId": job["id"]})
        revision = owner.call("GET", f"/api/stores/{store_id}/job-postings/{job['id']}", expect=200).json()["revision"]
        request = owner.call(
            "POST", f"/api/stores/{store_id}/job-postings/{job['id']}/applications/{application['id']}/work-requests",
            key=key(), json={"expectedJobRevision": revision}, expect=201).json()
        check(request["expiresAt"] == job["startAt"], "a request close to the start expires at the start")
        self.expect_notification("worker-b", "WORK_REQUEST_RECEIVED", {
            "type": "WORK_REQUEST", "requestId": request["id"], "storeId": store_id, "jobId": job["id"]})
        self.state.update(soon_job=job["id"], soon_request=request["id"],
                          soon_deadline=datetime.fromisoformat(job["startAt"]))

    def step_job_posting(self):
        owner, store_id = self.state["owner"], self.state["store_id"]
        idem = key()
        job = owner.call("POST", f"/api/stores/{store_id}/job-postings", json=self.job_body(2), key=idem,
                         expect=201).json()
        replay = owner.call("POST", f"/api/stores/{store_id}/job-postings", json=self.job_body(2), key=idem,
                            expect=201)
        check(replay.headers.get("Idempotent-Replayed") == "true" and replay.json()["id"] == job["id"],
              "a retried creation replays the first result", replay)
        check(job["status"] == "RECRUITING" and job["estimatedPay"] == 48000, "posting summary")
        self.state["job_id"] = job["id"]

    def step_applications(self):
        job_id, store_id = self.state["job_id"], self.state["store_id"]
        for role, intro in (("worker-a", "저녁 근무 가능합니다."), ("worker-b", "주말 위주지만 지원합니다.")):
            worker = self.state[role]
            found = worker.call("GET", "/api/job-postings", params={"q": "E2E 저녁", "workDay": "WEEK",
                                                                    "timeBand": "NIGHT"}, expect=200).json()
            check(any(item["id"] == job_id for item in found["items"]), f"{role} finds the posting")
            check(worker.call("GET", f"/api/job-postings/{job_id}", expect=200).json()["canApply"], f"{role} can apply")
            application = worker.call("POST", f"/api/job-postings/{job_id}/applications", key=key(),
                                      json={"introduction": intro}, expect=201).json()
            self.state[f"{role}-application"] = application["id"]
            again = worker.call("POST", f"/api/job-postings/{job_id}/applications", key=key(),
                                json={"introduction": intro}, expect=409)
            check(worker.error_code(again) == "APPLICATION_ALREADY_ACTIVE", "a second live application is refused")
            self.expect_notification("owner", "NEW_APPLICATION", {
                "type": "JOB_APPLICATION", "applicationId": application["id"], "storeId": store_id, "jobId": job_id})
        home = self.state["worker-a"].call("GET", "/api/users/me/home", expect=200).json()
        check(home["pendingApplicationCount"] == 1, "worker home counts the pending application")

    def send_request(self, role: str) -> dict:
        owner, store_id, job_id = self.state["owner"], self.state["store_id"], self.state["job_id"]
        base = f"/api/stores/{store_id}/job-postings/{job_id}"
        revision = owner.call("GET", base, expect=200).json()["revision"]
        request = owner.call("POST", f"{base}/applications/{self.state[f'{role}-application']}/work-requests",
                             key=key(), json={"expectedJobRevision": revision}, expect=201).json()
        check(request["status"] == "PENDING", "the work request waits for an answer")
        self.expect_notification(role, "WORK_REQUEST_RECEIVED", {
            "type": "WORK_REQUEST", "requestId": request["id"], "storeId": store_id, "jobId": job_id})
        return request

    def step_request_withdrawal(self):
        owner, store_id, job_id = self.state["owner"], self.state["store_id"], self.state["job_id"]
        base = f"/api/stores/{store_id}/job-postings/{job_id}"
        applicants = owner.call("GET", f"{base}/applications", expect=200).json()
        check([item["id"] for item in applicants["items"]] ==
              [self.state["worker-a-application"], self.state["worker-b-application"]], "applicants in order")
        request = self.send_request("worker-b")
        cancelled = owner.call("POST", f"{base}/work-requests/{request['id']}/withdrawal", key=key(),
                               json={"expectedRevision": request["revision"]}, expect=200).json()
        check(cancelled["status"] == "CANCELLED", "the owner withdrew the pending request")
        self.expect_notification("worker-b", "WORK_REQUEST_WITHDRAWN", {
            "type": "WORK_REQUEST", "requestId": request["id"], "storeId": store_id, "jobId": job_id})
        stale = self.state["worker-b"].call("POST", f"/api/users/me/work-requests/{request['id']}/response",
                                            json={"expectedRevision": 1, "decision": "ACCEPT"}, key=key(),
                                            expect=409)
        check(self.state["worker-b"].error_code(stale) == "WORK_REQUEST_NOT_PENDING", "a withdrawn request is closed")

    def accept(self) -> dict:
        worker, store_id, job_id = self.state["worker-a"], self.state["store_id"], self.state["job_id"]
        request = self.send_request("worker-a")
        idem = key()
        body = {"expectedRevision": 1, "decision": "ACCEPT"}
        accepted = worker.call("POST", f"/api/users/me/work-requests/{request['id']}/response", json=body, key=idem,
                               expect=200).json()
        check(accepted["status"] == "ACCEPTED" and accepted["accessGrant"]["type"] == "TEMPORARY",
              "acceptance confirms the shift with temporary access")
        replay = worker.call("POST", f"/api/users/me/work-requests/{request['id']}/response", json=body, key=idem,
                             expect=200)
        check(replay.headers.get("Idempotent-Replayed") == "true", "a retried acceptance replays", replay)
        again = worker.call("POST", f"/api/users/me/work-requests/{request['id']}/response", json=body, key=key(),
                            expect=409)
        check(worker.error_code(again) == "WORK_REQUEST_NOT_PENDING", "a second acceptance is refused", again)
        work_date = self.job_body(2)["workDate"]
        events = worker.call("GET", "/api/users/me/calendar/events", params={"month": work_date[:7]},
                             expect=200).json()
        event = [e for e in events["events"] if e["jobId"] == job_id]
        check(len(event) == 1, "the confirmed shift is on the worker calendar")
        target = {"type": "WORK_SCHEDULE", "eventId": event[0]["id"], "storeId": store_id, "workDate": work_date}
        for name in ("worker-a", "owner"):
            self.expect_notification(name, "WORK_CONFIRMED", target)
        return accepted

    def step_acceptance(self):
        self.state["accepted"] = self.accept()
        owner, store_id, job_id = self.state["owner"], self.state["store_id"], self.state["job_id"]
        base = f"/api/stores/{store_id}/job-postings/{job_id}"
        job = owner.call("GET", base, expect=200).json()
        check(job["status"] == "CLOSED" and job["closedAt"] is not None, "acceptance closed the posting")
        onboarding = owner.call("GET", f"{base}/onboarding", expect=200).json()
        check(onboarding["accessStatus"] == "ACTIVE" and onboarding["manualStatus"] == "NOT_PUBLISHED",
              "onboarding links the confirmed worker")
        other = self.state["worker-b"].call(
            "GET", f"/api/users/me/applications/{self.state['worker-b-application']}", expect=200).json()
        check(other["status"] == "NOT_SELECTED", "the other applicant is not selected")

    def step_confirmation_withdrawal(self):
        owner, store_id, job_id = self.state["owner"], self.state["store_id"], self.state["job_id"]
        base = f"/api/stores/{store_id}/job-postings/{job_id}"
        accepted = self.state["accepted"]
        withdrawn = owner.call("POST", f"{base}/work-requests/{accepted['id']}/confirmation-withdrawal", key=key(),
                               json={"expectedRevision": accepted["revision"]}, expect=200).json()
        check(withdrawn["status"] == "CONFIRMATION_WITHDRAWN" and withdrawn["accessGrant"]["status"] == "REVOKED",
              "the confirmation and its temporary access ended")
        job = owner.call("GET", base, expect=200).json()
        check(job["status"] == "RECRUITING" and job["closedAt"] is None, "the posting is open again")
        self.expect_notification("worker-a", "WORK_CONFIRMATION_WITHDRAWN", {
            "type": "WORK_REQUEST", "requestId": accepted["id"], "storeId": store_id, "jobId": job_id})
        restored = self.state["worker-b"].call(
            "GET", f"/api/users/me/applications/{self.state['worker-b-application']}", expect=200).json()
        check(restored["status"] == "APPLIED", "the applicant the acceptance ended is restored")
        missing = owner.call("GET", f"{base}/onboarding", expect=404)
        check(owner.error_code(missing) == "ONBOARDING_NOT_FOUND", "no confirmed worker after the withdrawal")
        self.state["accepted"] = self.accept()  # confirmed again for the rest of the demo

    def access_grants(self, role: str) -> list[tuple[str, str]]:
        """(type, status) of `role`'s grants at the store, newest first, from the worker's own view."""
        access = self.state[role].call("GET", f"/api/users/me/stores/{self.state['store_id']}/access",
                                       expect=200).json()["access"]
        check(access["accessStatus"] == "ACTIVE", f"{role}'s access is active")
        return [(grant["type"], grant["status"]) for grant in access["accessGrants"]]

    def step_mail_link_acceptance(self):
        """Worker A already holds TEMPORARY access from the confirmed shift; the regular
        invitation is still accepted and both grants coexist."""
        if self.mailbox is None:
            raise StepFailed("no mailbox: the runner needs its SMTP server")
        owner, worker, store_id = self.state["owner"], self.state["worker-a"], self.state["store_id"]
        invitation = self.state["worker-a-invitation"]
        before = self.access_grants("worker-a")
        check(before == [("TEMPORARY", "ACTIVE"), ("TEMPORARY", "REVOKED")],
              f"before the link worker A has only the shift's access: {before}")
        message = self.mailbox.wait_for(self.state["worker-a-email"], MAIL_WAIT_SECONDS)
        check(message is not None, f"the invitation mail arrives within {MAIL_WAIT_SECONDS} s")
        store_name = self.state["store_name"]
        check(message["Subject"] == f"[Jidan] {store_name} 근무자 초대", f"mail subject: {message['Subject']}")
        text = text_of(message)
        check(f"{store_name}에서 Jidan 근무자로 초대했습니다." in text, "the mail names the store")
        check(f"초대 링크: {seoul_text(invitation['expiresAt'])} 전까지 사용할 수 있습니다." in text,
              "the mail states the link expiry in Seoul time")
        links = [line for line in text.splitlines() if line.startswith(self.origin + "/invitations/accept#token=")]
        check(len(links) == 1, "the mail carries one invitation link with the token in the fragment")
        token = token_from_link(links[0])
        preview = worker.call("POST", "/api/store-invitations/preview", json={"token": token}, expect=200).json()
        check(preview["invitationId"] == invitation["id"] and preview["status"] == "PENDING"
              and preview["store"]["id"] == store_id, "the link previews this pending invitation")
        accepted = worker.call("POST", "/api/store-invitations/accept", json={"token": token}, expect=200).json()
        check(accepted["workerId"] == self.state["worker-a-id"] and accepted["accessGrant"]["type"] == "REGULAR",
              "a TEMPORARY grant does not block the regular invitation")
        after = self.access_grants("worker-a")
        check(sorted(after) == [("REGULAR", "ACTIVE"), ("TEMPORARY", "ACTIVE"), ("TEMPORARY", "REVOKED")],
              f"regular and temporary access coexist: {after}")
        stores = worker.call("GET", "/api/users/me/stores", expect=200).json()
        mine = [item for item in stores["items"] if item["store"]["id"] == store_id]
        check(len(mine) == 1, "the store is listed once however many grants")
        self.expect_notification("owner", "INVITATION_ACCEPTED", {"type": "STORE", "storeId": store_id})
        workers = owner.call("GET", f"/api/stores/{store_id}/workers", expect=200).json()["items"]
        check(len(workers) == 2, "the owner sees two workers")
        detail = owner.call("GET", f"/api/stores/{store_id}/workers/{self.state['worker-a-id']}", expect=200).json()
        check(sorted(g["type"] for g in detail["accessGrants"]) == ["REGULAR", "TEMPORARY", "TEMPORARY"],
              "the owner's worker detail keeps both kinds of grant")

    def step_home_and_calendar(self):
        work_date = self.job_body(2)["workDate"]
        job_id = self.state["job_id"]
        worker_events = self.state["worker-a"].call("GET", "/api/users/me/calendar/events",
                                                    params={"month": work_date[:7]}, expect=200).json()["events"]
        check([e["jobId"] for e in worker_events] == [job_id], "one active shift on the worker calendar")
        owner_events = self.state["owner"].call("GET", "/api/owners/me/calendar/events",
                                                params={"month": work_date[:7]}, expect=200).json()["events"]
        check([e["jobId"] for e in owner_events] == [job_id], "one active shift on the owner calendar")
        home = self.state["owner"].call("GET", "/api/owners/me/home", expect=200).json()
        check(home["selectedStoreId"] == self.state["store_id"], "owner home selects the store")
        check(home["unreadNotificationCount"] == len(self.notifications("owner")), "owner home unread count")
        worker_home = self.state["worker-a"].call("GET", "/api/users/me/home", expect=200).json()
        check(worker_home["pendingApplicationCount"] == 0 and worker_home["regularStoreCount"] == 1, "worker home")
        check(worker_home["unreadNotificationCount"] == len(self.notifications("worker-a")), "worker home unread count")

    def step_unanswered_request(self):
        owner, store_id = self.state["owner"], self.state["store_id"]
        job_id, request_id, deadline = self.state["soon_job"], self.state["soon_request"], self.state["soon_deadline"]
        wait = (deadline - datetime.now(UTC)).total_seconds() + 1
        if wait > 0:
            time.sleep(wait)
        base = f"/api/stores/{store_id}/job-postings/{job_id}"
        requests = owner.call("GET", f"{base}/work-requests", expect=200).json()["items"]
        check([(r["id"], r["status"]) for r in requests] == [(request_id, "EXPIRED")],
              "from its deadline the request reads EXPIRED")
        revision = owner.call("GET", base, expect=200).json()["revision"]
        closed = owner.call("POST", f"{base}/closure", key=key(), json={"expectedRevision": revision},
                            expect=200).json()
        check(closed["status"] == "CLOSED", "the owner closes the posting without a selection")
        self.expect_notification("owner", "WORK_REQUEST_NO_RESPONSE", {
            "type": "WORK_REQUEST", "requestId": request_id, "storeId": store_id, "jobId": job_id})
        late = self.state["worker-b"].call("POST", f"/api/users/me/work-requests/{request_id}/response",
                                           json={"expectedRevision": 1, "decision": "ACCEPT"}, key=key(), expect=409)
        check(self.state["worker-b"].error_code(late) == "WORK_REQUEST_EXPIRED", "an expired request cannot be accepted")

    def step_read_notifications(self):
        owner = self.state["owner"]
        for name in ("owner", "worker-a", "worker-b", "worker-c"):
            body = self.state[name].call("GET", "/api/users/me/notifications", params={"read": "UNREAD", "size": 100},
                                         expect=200).json()
            relevant = [i for i in body["items"] if i["type"] not in CLOCK_TYPES]
            check(len(relevant) == sum(self.expected.get(name, Counter()).values()), f"{name} unread list")
        before = owner.call("GET", "/api/users/me/notifications", params={"read": "UNREAD"}, expect=200).json()
        first = before["items"][0]
        idem = key()
        read = owner.call("POST", f"/api/users/me/notifications/{first['id']}/read", json={}, key=idem,
                          expect=200).json()
        check(read["readAt"] is not None, "the notification is read")
        replay = owner.call("POST", f"/api/users/me/notifications/{first['id']}/read", json={}, key=idem, expect=200)
        check(replay.headers.get("Idempotent-Replayed") == "true", "a retried read replays", replay)
        after = owner.call("GET", "/api/users/me/notifications", params={"read": "UNREAD"}, expect=200).json()
        check(after["unreadCount"] == before["unreadCount"] - 1, "unreadCount drops by one")
        check(first["id"] not in [i["id"] for i in after["items"]], "the read notification left the unread list")
        home = owner.call("GET", "/api/owners/me/home", expect=200).json()
        check(home["unreadNotificationCount"] == after["unreadCount"], "home agrees with the notification list")
        self.state["notification_counts"] = {name: dict(counter) for name, counter in self.expected.items()}

    def step_security(self):
        owner, store_id = self.state["owner"], self.state["store_id"]
        url = f"/api/stores/{store_id}/job-postings"
        no_token = owner.call("POST", url, json=self.job_body(2), key=key(), headers={"X-CSRF-Token": ""}, expect=403)
        check(owner.error_code(no_token) == "CSRF_INVALID", "a write without the CSRF token is refused", no_token)
        evil = owner.call("POST", url, json=self.job_body(2), key=key(), headers={"Origin": "https://evil.example"},
                          expect=403)
        check(owner.error_code(evil) == "CSRF_INVALID", "a foreign Origin is refused", evil)
        stranger = self.actor()
        check(stranger.call("GET", "/api/owners/me/home", expect=401).status_code == 401, "no session is 401")
        worker = self.state["worker-b"]
        hidden = worker.call("GET", f"/api/users/me/applications/{self.state['worker-a-application']}", expect=404)
        check(worker.error_code(hidden) == "RESOURCE_NOT_FOUND", "another worker's application is hidden")
        notification = self.notifications("owner")[0]
        foreign = worker.call("POST", f"/api/users/me/notifications/{notification['id']}/read", json={}, key=key(),
                              expect=404)
        check(worker.error_code(foreign) == "RESOURCE_NOT_FOUND", "another member's notification is hidden")

    # -- running -------------------------------------------------------------------------------

    STEPS = (
        ("health", "step_health"), ("owner sign-up", "step_owner_signup"),
        ("pending owner limits", "step_pending_owner_is_limited"), ("admin approval", "step_admin_approval"),
        ("worker sign-ups", "step_worker_signups"), ("invitations", "step_invitations"),
        ("inbox acceptance", "step_inbox_acceptance"), ("unanswered request setup", "step_unanswered_request_setup"),
        ("job posting", "step_job_posting"), ("applications", "step_applications"),
        ("request withdrawal", "step_request_withdrawal"), ("acceptance", "step_acceptance"),
        ("confirmation withdrawal", "step_confirmation_withdrawal"),
        # After worker A's shift is confirmed: the regular invitation coexists with TEMPORARY access.
        ("mail link acceptance", "step_mail_link_acceptance"), ("home and calendar", "step_home_and_calendar"),
        ("unanswered request", "step_unanswered_request"), ("notifications read", "step_read_notifications"),
        ("security", "step_security"),
    )

    # Steps that wait in real time for a work request deadline (about two minutes).
    REALTIME_STEPS = frozenset({"step_unanswered_request_setup", "step_unanswered_request"})

    def steps(self) -> list[tuple[str, str]]:
        jobs = [(label, method) for label, method in self.STEPS
                if self.realtime_expiry or method not in self.REALTIME_STEPS]
        manual = [(label, method) for label, method in self.MANUAL_STEPS
                  if not (self.live and method in self.FAKE_ONLY_STEPS)] if self.manual else []
        return jobs + manual

    def step_health(self):
        response = httpx.get(self.base_url + "/api/health", timeout=10)
        check(response.status_code == 200 and response.json().get("database") == "ok", "health with database")

    def execute(self) -> bool:
        failed = False
        for label, method in self.steps():
            if failed:
                self.results.append(("SKIP", label, ""))
                continue
            started = time.monotonic()
            try:
                getattr(self, method)()
                self.results.append(("PASS", label, f"{time.monotonic() - started:.1f}s"))
            except StepFailed as error:
                failed = True
                detail = str(error)
                if error.response is not None and error.response.headers.get("content-type", "").startswith(
                        "application/json"):
                    body = error.response.json()
                    detail += f" (code={body.get('code')}, requestId={body.get('requestId')})"
                self.results.append(("FAIL", label, detail))
            except Exception as error:  # noqa: BLE001 - any error fails this step and is reported
                # An unexpected response shape, a dropped connection or a database error while
                # preparing a session: the run still reports every step instead of a bare traceback.
                failed = True
                self.results.append(("FAIL", label, f"{type(error).__name__}: {str(error)[:300]}"))
        return not failed

    def report(self) -> None:
        print(f"\ndemo scenario run {self.run} against {self.base_url}")
        if not self.realtime_expiry:
            print("  (real-time work request expiry left out: --no-realtime-expiry)")
        for status, label, detail in self.results:
            print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
        if self.expected:
            print(f"  notifications by recipient: {({n: dict(c) for n, c in self.expected.items()})}")
        passed = sum(1 for status, *_ in self.results if status == "PASS")
        print(f"result: {passed}/{len(self.results)} steps passed")


def server_env(origin: str, smtp_port: int, smtp_host: str = "127.0.0.1", *, ai: str = "fake",
               media_root: str | None = None, ai_counter: str | None = None, ai_call_limit: int = 20,
               ai_live_ops: str = ",".join(ai_scenario.DEFAULT_LIVE_OPS)) -> tuple[dict[str, str], str]:
    """Environment for a server under test, and the admin password whose hash it carries.

    AI work runs in the server's background task runner on `e2e.ai_scenario` (fake by default);
    `ai="live"` sends `ai_live_ops` to OpenAI (OPENAI_API_KEY from the caller's environment)."""
    from cryptography.fernet import Fernet

    from app.admin_password import hash_password

    password = secrets.token_urlsafe(18)
    return {
        "APP_ENV": "local", "ALLOWED_ORIGINS": origin, "FRONTEND_ORIGIN": origin,
        "ADMIN_PASSWORD_HASH": hash_password(password), "INVITATION_MAIL_KEY": Fernet.generate_key().decode(),
        "INVITATION_MAIL_BACKEND": "smtp", "SMTP_HOST": smtp_host, "SMTP_PORT": str(smtp_port),
        "SMTP_SECURITY": "none", "MAIL_FROM": "Jidan <no-reply@jidan.example>", "COOKIE_SECURE": "false",
        "BACKGROUND_JOBS": "on", "TASK_RUNNER_MODE": "background",
        "INTERVIEW_GUIDANCE_RESPONSES": "on",  # OpenAPI 0.11.0 question guidance fields
        "E2E_AI": ai, "AI_PROVIDER": "openai" if ai == "live" else "fake",
        **({"MEDIA_ROOT": media_root} if media_root else {}),
        **({"E2E_AI_CALL_LIMIT": str(ai_call_limit), "E2E_AI_LIVE_OPS": ai_live_ops,
            **({"E2E_AI_COUNTER_FILE": ai_counter} if ai_counter else {})} if ai == "live" else {}),
    }, password


def start_server(port: int, origin: str, smtp_port: int, **ai_options) -> tuple[subprocess.Popen, str]:
    extra, password = server_env(origin, smtp_port, **ai_options)
    env = {**os.environ, **extra}
    process = subprocess.Popen([sys.executable, "-m", "e2e.serve", "--port", str(port)], cwd=BACK_END, env=env)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return process, password
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise SystemExit("the server exited during start-up")
        time.sleep(0.3)
    process.terminate()
    raise SystemExit("the server did not become healthy in 30 seconds")


def cleanup() -> int:
    """Delete the runner's accounts and their data; the caller checks the target first."""
    with operator_cli(), session_scope() as db:
        return demo_seed.delete_accounts(db, SUB_PREFIX)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-server", action="store_true", help="run python -m e2e.serve for this run")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--smtp-port", type=int, default=0, help="where the server under test sends mail")
    parser.add_argument("--smtp-listen", default="127.0.0.1", help="0.0.0.0 when the server runs in Docker")
    parser.add_argument("--no-realtime-expiry", action="store_true",
                        help="leave out the unanswered request that waits about two minutes for its deadline")
    parser.add_argument("--cleanup", action="store_true",
                        help="delete every account this runner created (and their data), then exit")
    parser.add_argument("--skip-manual", action="store_true", help="only the jobs steps, no manual/AI steps")
    parser.add_argument("--ai", choices=("fake", "live"), default="fake",
                        help="live sends some AI operations to OpenAI (needs JIDAN_E2E_OPENAI=1 and OPENAI_API_KEY)")
    parser.add_argument("--ai-call-limit", type=int, default=20, help="most OpenAI calls one live run may make")
    parser.add_argument("--ai-live-ops", default=",".join(ai_scenario.DEFAULT_LIVE_OPS),
                        help="AI operations sent to OpenAI in live mode; the rest stay on the fake")
    args = parser.parse_args()
    if args.ai == "live" and (os.getenv("JIDAN_E2E_OPENAI") != "1" or not os.getenv("OPENAI_API_KEY", "").strip()):
        print("--ai live needs JIDAN_E2E_OPENAI=1 and OPENAI_API_KEY (it is billed)", file=sys.stderr)
        return 2
    if args.ai == "live" and not args.start_server:
        print("--ai live needs --start-server (the call budget lives in that server process)", file=sys.stderr)
        return 2
    try:
        demo_seed.check_target()
    except demo_seed.UnsafeTarget as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.cleanup:
        print(f"deleted {cleanup()} E2E accounts and their data")
        return 0
    mailbox = Mailbox(args.smtp_listen, args.smtp_port).start()
    process = None
    workdir = tempfile.TemporaryDirectory(prefix="jidan-e2e-")
    counter = os.path.join(workdir.name, "ai-calls.json")
    try:
        if args.start_server:
            if args.ai == "live":
                print(f"live AI: at most {args.ai_call_limit} OpenAI calls for {args.ai_live_ops}")
            process, password = start_server(
                args.port, args.origin, mailbox.port, ai=args.ai, media_root=os.path.join(workdir.name, "media"),
                ai_counter=counter, ai_call_limit=args.ai_call_limit, ai_live_ops=args.ai_live_ops)
            base_url = f"http://127.0.0.1:{args.port}"
        else:
            password = os.getenv("E2E_ADMIN_PASSWORD", "")
            base_url = args.base_url or f"http://127.0.0.1:{args.port}"
            if not password:
                print("E2E_ADMIN_PASSWORD is required without --start-server", file=sys.stderr)
                return 2
        fake_kakao = args.start_server and not os.getenv("KAKAO_REST_API_KEY", "").strip()
        scenario = Scenario(base_url, args.origin, password, fake_kakao, mailbox,
                            realtime_expiry=not args.no_realtime_expiry, manual=not args.skip_manual, ai=args.ai)
        ok = scenario.execute()
        scenario.report()
        if args.ai == "live" and os.path.exists(counter):
            print(f"  OpenAI calls: {Path(counter).read_text()}")
        return 0 if ok else 1
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        mailbox.stop()
        workdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
