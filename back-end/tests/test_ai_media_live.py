"""Real OpenAI check of writing a section from attached photos. Opt-in only: OPENAI_API_KEY and
JIDAN_RUN_OPENAI=1 (see conftest).

Two synthetic images are drawn here with Pillow (no binary fixtures): a fridge shelf diagram with
Korean labels and a closing sign. The model (gpt-6-luna, writing effort medium) must write steps
for the target section that reflect the visible text and cite the image that shows it.
"""

import io
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.ai import DEFAULT_MODEL
from app.ai.contracts import (
    ImageInput,
    MediaEvidence,
    MediaWritingRequest,
    RevisionTarget,
    SectionItem,
    StepItem,
    StructureSnapshot,
)
from app.ai.openai_provider import OpenAiProvider

pytestmark = pytest.mark.openai

FONTS = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)
SEC, OTHER, KEEP, OTHER_STEP = (f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 5))
SHELF = "media:00000000-0000-4000-8000-000000000011"
SIGN = "media:00000000-0000-4000-8000-000000000012"


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = next((p for p in FONTS if Path(p).exists()), None)
    if path is None:
        pytest.skip("no Korean font to draw the synthetic photos")
    return ImageFont.truetype(path, size)


def _jpeg(image: Image.Image) -> ImageInput:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return ImageInput(mime_type="image/jpeg", data=out.getvalue())


def shelf_diagram() -> ImageInput:
    image = Image.new("RGB", (1024, 768), "white")
    draw = ImageDraw.Draw(image)
    title, label = _font(48), _font(40)
    draw.text((40, 30), "냉장 진열대 (우유)", fill="black", font=title)
    draw.rectangle((60, 140, 960, 560), outline="black", width=6)
    draw.line((60, 350, 960, 350), fill="black", width=4)
    for x in range(120, 900, 140):  # milk cartons
        draw.rectangle((x, 380, x + 80, 540), outline="navy", width=4)
    draw.text((90, 170), "먼저 들어온 우유 → 앞", fill="darkred", font=label)
    draw.text((90, 250), "나중에 들어온 우유 → 뒤", fill="darkblue", font=label)
    draw.rectangle((60, 600, 960, 720), fill="lightyellow", outline="orange", width=4)
    draw.text((90, 630), "진열 전에 유통기한 확인", fill="black", font=label)
    return _jpeg(image)


def closing_sign() -> ImageInput:
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 870, 470), outline="red", width=10)
    draw.text((110, 170), "마감 후 금고 잠금", fill="red", font=_font(84))
    return _jpeg(image)


class CapturingProvider(OpenAiProvider):
    raw: list[str]

    def _complete(self, *args):
        self.raw = super()._complete(*args)
        return self.raw


def test_live_section_is_written_from_photos_with_media_citations():
    current = StructureSnapshot(sections=(
        SectionItem(id=SEC, category="COMMON_TASK", title="우유 진열과 마감", steps=(
            StepItem(id=KEEP, instruction="입고된 우유를 창고에서 냉장 진열대로 옮겨요."),)),
        SectionItem(id=OTHER, category="COMMON_TASK", title="손님 응대", steps=(
            StepItem(id=OTHER_STEP, instruction="손님이 들어오면 인사해요."),)),
    ))
    media = (
        MediaEvidence(id=SHELF, kind="PHOTO", title="우유 진열대", caption=None, image=shelf_diagram()),
        MediaEvidence(id=SIGN, kind="PHOTO", title="금고 앞 안내문", caption="마감 때 꼭 봐 주세요", image=closing_sign()),
    )
    provider = CapturingProvider(
        api_key=os.environ["OPENAI_API_KEY"], model=os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
        transcribe_model="gpt-transcribe", writing_effort="medium", timeout_seconds=60.0,
        writing_timeout_seconds=240.0)
    started = time.monotonic()
    result = provider.write_section_from_media(MediaWritingRequest(
        current=current, target=RevisionTarget(kind="SECTION", target_id=SEC), media=media))
    elapsed = time.monotonic() - started

    raw = next(json.loads(c) for c in provider.raw if c.strip().startswith("{"))
    [raw_target] = [s for s in raw["structure"]["sections"] if s["ref"] == SEC]
    print(f"\nlatency={elapsed:.1f}s config={result.meta.config_version} outcome={raw['outcome']}")
    for raw_step in raw_target["steps"]:
        print(f"  {raw_step['ref']}: {raw_step['instruction']} cites={raw_step['evidence_ids']} "
              f"checklist={raw_step['checklist_item']}")
    print(f"  removed_steps={raw['removed_steps']}")

    assert result.outcome == "APPLIED"
    new_raw = [s for s in raw_target["steps"] if s["ref"].startswith("new-")]
    assert new_raw and all(s["evidence_ids"] and set(s["evidence_ids"]) <= {SHELF, SIGN} for s in new_raw)
    [section, other] = result.structure.sections
    assert other == current.sections[1]  # only the target changed
    assert any(step.id == KEEP for step in section.steps)  # the existing step survived
    texts = {s["instruction"]: set(s["evidence_ids"]) for s in new_raw}
    assert any("유통기한" in text and SHELF in ids for text, ids in texts.items())
    assert any(("먼저" in text or "앞" in text) and SHELF in ids for text, ids in texts.items())
    assert any("금고" in text and SIGN in ids for text, ids in texts.items())
