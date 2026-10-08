
import pytest

from app.ai.contracts import (
    PhotoSuggestionsRequest,
    StructureSnapshot,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.provider import FallbackAiProvider
from app.ai.schemas import OUTPUTS


def photo_request():
    return PhotoSuggestionsRequest(summary="입구에 열쇠가 있어요.", structure=StructureSnapshot())


def test_photo_operation_empty_and_grouped_nullable_output():
    provider = FakeAiProvider()
    assert provider.suggest_review_photos(photo_request()).suggestions == ()
    raw = {"suggestions": [{"sectionId": "saved-section", "title": "위치 사진", "items": [
        {"label": "열쇠 위치", "description": None}], "footer": None}]}
    provider.script("suggest_review_photos", FakeOutcome.ok(raw))
    result = provider.suggest_review_photos(photo_request())
    assert result.suggestions[0].section_id == "saved-section"
    assert result.suggestions[0].items[0].description is None


@pytest.mark.parametrize("key", ["helpful", "revision", "attachmentTarget", "status"])
def test_photo_operation_rejects_model_owned_server_fields(key):
    provider = FakeAiProvider().script("suggest_review_photos", FakeOutcome.ok({"suggestions": [], key: True}))
    with pytest.raises(AiError):
        provider.suggest_review_photos(photo_request())


def test_photo_operation_fallback_and_operation_registries():
    primary = FakeAiProvider().script("suggest_review_photos", FakeOutcome.fail(AiErrorCode.TIMEOUT))
    assert FallbackAiProvider(primary, FakeAiProvider()).suggest_review_photos(photo_request()).suggestions == ()
    from app.ai.fake import OPERATIONS
    from app.ai.openai_provider import MAX_OUTPUT_TOKENS
    from app.ai.prompts import INSTRUCTIONS
    assert set(OUTPUTS) == set(INSTRUCTIONS) == set(MAX_OUTPUT_TOKENS)
    assert set(OUTPUTS) <= set(OPERATIONS)


