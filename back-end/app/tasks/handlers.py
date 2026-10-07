"""Imports every module that registers task handlers.

The background runner imports this at startup; add one import per domain module.
"""

import app.interview.tasks
import app.manual_corrections
import app.media.transcription
import app.qa.answers  # noqa: F401 - QA_ANSWER
