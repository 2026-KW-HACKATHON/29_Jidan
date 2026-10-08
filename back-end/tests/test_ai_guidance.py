
import pytest

from app.ai.contracts import (
    QuestionRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.schemas import RawQuestion, parse_question
from tests.test_ai_provider import INTENT


def output():
    return {"question": "어떤 일을 하나요?", "guidance": None, "guidanceCards": [{
        "type": "LIST", "title": "예시", "footer": None,
        "items": [{"id": None, "label": "청소", "description": None, "status": None}],
    }]}


def test_mixed_invalid_cards_keep_question_and_valid_card():
    raw = output()
    raw["guidanceCards"] += [{"type": "PHOTO_SUGGESTIONS"}, {"type": "LIST", "id": "invented"}]
    raw["guidance"] = 123
    provider = FakeAiProvider().script("generate_question", FakeOutcome.ok(raw))
    question = provider.generate_question(QuestionRequest(kind="BASE", intent=INTENT, depth=0))
    assert question.text == raw["question"] and question.guidance is None
    assert len(question.guidance_cards) == 1
    assert question.guidance_cards[0]["items"][0]["id"] is None
    assert "id" not in question.guidance_cards[0]


@pytest.mark.parametrize("value", [None, "", " ", 5, "가" * 2001])
def test_invalid_question_stays_retryable(value):
    raw = output()
    raw["question"] = value
    provider = FakeAiProvider().script("generate_question", FakeOutcome.ok(raw))
    with pytest.raises(AiError) as error:
        provider.generate_question(QuestionRequest(kind="BASE", intent=INTENT, depth=0))
    assert error.value.code == AiErrorCode.INVALID_OUTPUT and error.value.retryable


def test_raw_question_strict_required_keys_and_tolerant_legacy_parser():
    with pytest.raises(ValueError):
        RawQuestion.model_validate({"question": "질문"})
    assert parse_question({"question": "질문"}).guidanceCards == []


@pytest.mark.parametrize("guidance", ["\x00", "   ", "가" * 2001, 5, [], {}])
def test_invalid_guidance_degrades_to_none(guidance):
    raw = output()
    raw["guidance"] = guidance
    provider = FakeAiProvider().script("generate_question", FakeOutcome.ok(raw))
    result = provider.generate_question(QuestionRequest(kind="BASE", intent=INTENT, depth=0))
    assert result.guidance is None and len(result.guidance_cards) == 1
