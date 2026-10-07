"""Demo seed step: a published manual for the demo cafe (registered in app.demo_seed.EXTRA_SEEDS).

Without it the worker manual and AI Q&A screens of the demo have nothing to show. The content is
static (no AI call) and goes through the same server path as a generated draft and a publication:
`ContentIn` validation, `prepare_content`, `write_initial_content`, then `publish_draft`, which also
acknowledges the open gap and records MANUAL_PUBLISHED for every current reader (김지수). IDs are
fixed so a reseed produces identical rows; `app.manual_demo_reset` removes them on reset.

The "포스 마감" section is what the E2E fake answers about (`e2e.ai_scenario`), so a question about
"포스" in the demo returns a grounded answer with citations.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import ManualVersion, Notification, Store, StoreManual
from app.manual_drafts import publish_draft
from app.manual_editing import ContentIn, prepare_content, write_initial_content

NAMESPACE = uuid.UUID("5b0f8a1e-2c6d-4e5f-9a7b-1c2d3e4f5a6b")
PUBLISHED_DAYS_AGO = 3


def manual_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def _steps(section: str, *instructions: str, checklist: tuple[int, ...] = ()) -> list[dict]:
    return [{"id": manual_id(f"step:{section}:{index}"), "instruction": text, "checklistItem": index in checklist}
            for index, text in enumerate(instructions)]


def demo_content() -> dict:
    """Two shifts, three common tasks (one still without steps), a shift task and a rule."""
    opening, closing = manual_id("shift:open"), manual_id("shift:close")
    drinks = manual_id("section:drinks")
    return {
        "shifts": [
            {"id": opening, "name": "오픈조", "startTime": "09:00", "endTime": "15:00", "endsNextDay": False},
            {"id": closing, "name": "마감조", "startTime": "15:00", "endTime": "23:00", "endsNextDay": False},
        ],
        "sections": [
            {"id": manual_id("section:pos"), "category": "COMMON_TASK", "shiftId": None, "title": "포스 마감",
             "steps": _steps("pos", "포스 화면에서 마감 정산 버튼을 눌러요.", "출력한 영수증을 금고에 넣어요.",
                             "현금과 영수증 금액이 맞는지 확인해요.", checklist=(2,)),
             "photos": []},
            {"id": manual_id("section:cleaning"), "category": "COMMON_TASK", "shiftId": None, "title": "매장 청소",
             "steps": _steps("cleaning", "테이블과 의자를 닦아요.", "바닥을 쓸고 물걸레질해요.", "쓰레기는 분리해서 버려요."),
             "photos": []},
            {"id": drinks, "category": "COMMON_TASK", "shiftId": None, "title": "음료 레시피",
             "steps": [], "photos": []},
            {"id": manual_id("section:open"), "category": "SHIFT_TASK", "shiftId": opening, "title": "오픈 준비",
             "steps": _steps("open", "매장 불을 켜고 커피 머신을 예열해요.", "냉장고 온도를 확인해요."),
             "photos": []},
            {"id": manual_id("section:dress"), "category": "RULE", "shiftId": None, "title": "복장 규정",
             "steps": _steps("dress", "근무 중에는 앞치마를 착용해요.", "휴대폰은 보관함에 넣어요."),
             "photos": []},
        ],
        "structurePhotos": [],
        "missingInformation": [
            {"id": manual_id("missing:drinks"), "target": "SECTION", "targetId": drinks, "field": "steps",
             "description": "음료 레시피는 점주님이 정리하고 있어요. 만드는 방법은 점주님께 확인해 주세요."},
        ],
    }


def seed_published_manual(db: Session, ids: dict[str, str], now: datetime) -> None:
    """Publish `demo_content()` for the demo cafe, as its owner, a few days before `now`."""
    store = db.get(Store, ids["cafe"])
    at = now - timedelta(days=PUBLISHED_DAYS_AGO)
    manual = StoreManual(id=manual_id("manual:cafe"), store_id=store.id, created_at=at, updated_at=at)
    db.add(manual)
    db.flush()
    draft = ManualVersion(id=manual_id("version:cafe:1"), manual_id=manual.id, revision_no=1,
                          created_by_owner_id=store.owner_id, created_at=at, updated_at=at)
    db.add(draft)
    db.flush()
    content = ContentIn.model_validate(demo_content())
    write_initial_content(db, draft, prepare_content(db, draft, store.id, content), now=at)
    draft.generation_status = "READY"
    db.flush()
    owner = SimpleNamespace(user_id=store.owner_id)
    publish_draft(db, owner, store, manual, draft, [manual_id("missing:drinks")], now=at)
    # Like the core seed: notifications older than a day were already seen.
    db.execute(update(Notification).where(Notification.event_type == "MANUAL_PUBLISHED",
                                          Notification.target_id == store.id).values(read_at=at + timedelta(hours=1)))
    db.flush()
