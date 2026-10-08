"""Worker AI Q&A (#121): conversations, questions and answers, question media and transcription.

`router` carries every `/api/stores/{storeId}/manual/qa/...` operation.
"""

from fastapi import APIRouter

from app.qa.conversations import router as conversations_router
from app.qa.media import router as media_router
from app.qa.questions import router as questions_router

router = APIRouter()
router.include_router(conversations_router)
router.include_router(questions_router)
router.include_router(media_router)
