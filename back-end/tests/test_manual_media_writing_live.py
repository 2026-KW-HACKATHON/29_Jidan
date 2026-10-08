"""Live: a review section written through the HTTP API from a photo and a video (real OpenAI).

The interview itself runs on the fake (its answers are fixed, the COMMON_TASKS section is "손님 응대":
인사해요 / 주문을 받아요); only the operation under test, `write_section_from_media`, and
transcription go to OpenAI through the e2e router. The photo is a drawn counter sign and the video a
short clip of drawn signs (no sound), so what the model can read is known. Media unrelated to the
section (a milk shelf diagram) must change nothing. Run with OPENAI_API_KEY and JIDAN_RUN_OPENAI=1.
"""

import io
import time

import av
import pytest
from PIL import Image, ImageDraw

from app.ai import build_provider_from_env, set_ai_provider
from app.ai.fake import FakeAiProvider
from app.media.storage import set_media_storage
from e2e import ai_scenario
from tests.test_ai_media_live import _font, shelf_diagram
from tests.test_manual_media_writing import (
    COMMON,
    _driver,
    common_review,
    review,
    section_of,
    upload,
    write,
)

pytestmark = pytest.mark.openai

SIGNS = ("손님이 들어오면 \"어서 오세요\" 하고 인사해요", "주문 메뉴를 손님에게 다시 한 번 읽어 드려요")


def counter_sign() -> bytes:
    image = Image.new("RGB", (1024, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "카운터 안내", fill="black", font=_font(56))
    draw.rectangle((40, 160, 984, 520), outline="darkgreen", width=8)
    draw.text((80, 230), "결제 후 영수증과 진동벨을", fill="black", font=_font(48))
    draw.text((80, 320), "함께 드려요", fill="black", font=_font(48))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def sign_video() -> bytes:
    """Two seconds per sign, 2 fps H.264, no audio track."""
    out = io.BytesIO()
    with av.open(out, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = 960, 540, "yuv420p"
        for text in SIGNS:
            image = Image.new("RGB", (960, 540), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 20, 940, 520), outline="navy", width=8)
            draw.text((60, 230), text, fill="black", font=_font(52))
            for _ in range(4):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return out.getvalue()


@pytest.fixture
def live_flow(api, db_engine, fake_ai, tmp_path):
    fake = FakeAiProvider()
    live = build_provider_from_env()
    router = ai_scenario.RoutedAiProvider(fake, live, ("write_section_from_media", "transcribe"),
                                          ai_scenario.CallBudget(4, None))
    set_ai_provider(router)
    yield _driver(api, db_engine, fake, tmp_path)
    set_media_storage(None)
    set_ai_provider(None)


def test_live_review_section_is_written_from_a_photo_and_a_video(live_flow):
    drv = live_flow
    sign = upload(drv, counter_sign(), "MANUAL_PHOTO")
    clip = upload(drv, sign_video(), "MANUAL_VIDEO")
    assert sign.status_code == clip.status_code == 201, (sign.text, clip.text)
    sid, before = common_review(drv)
    started = time.monotonic()
    accepted = write(drv, sid, [sign.json()["id"], clip.json()["id"]])
    assert accepted.status_code == 202, accepted.text
    drv.run()
    elapsed = time.monotonic() - started
    done = review(drv, sid, COMMON)
    assert done["status"] == "READY", done.get("error")
    old = [step["instruction"] for step in section_of(before)["steps"]]
    steps = [step["instruction"] for step in section_of(done)["steps"]]
    print(f"\nmedia writing {elapsed:.1f}s")
    for text in steps:
        print(" -", text)
    # Existing steps stay (an existing step may be made concrete when a media item shows how,
    # e.g. 인사해요 → "어서 오세요"라고 인사해요, which the server accepts only with a citation).
    assert len(steps) > len(old) and "주문을 받아요." in steps
    assert any("인사" in text for text in steps)
    joined = " ".join(text for text in steps if text not in old)
    assert "영수증" in joined or "진동벨" in joined  # read from the photo
    assert "어서 오세요" in joined or "다시" in joined  # read from the video frames
    # Text only: nothing is attached to the manual (user decision 2026-10-08).
    assert section_of(done)["photos"] == [] and done["content"]["structurePhotos"] == []


def test_live_media_unrelated_to_the_section_changes_nothing(live_flow):
    drv = live_flow
    shelf = upload(drv, shelf_diagram().data, "MANUAL_PHOTO")
    assert shelf.status_code == 201, shelf.text
    sid, before = common_review(drv)
    assert write(drv, sid, [shelf.json()["id"]]).status_code == 202
    drv.run()
    done = review(drv, sid, COMMON)
    assert done["status"] == "READY", done.get("error")
    assert section_of(done)["steps"] == section_of(before)["steps"]
