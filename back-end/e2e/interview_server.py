"""Real app.main server with only the external AI provider supplied by a test factory."""
import argparse
import importlib
import os

import uvicorn


def default_provider():
    from app.ai.fake import FakeAiProvider
    return FakeAiProvider()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    from e2e.conftest import require_local_test_database
    require_local_test_database()
    if os.getenv("TASK_RUNNER_MODE") != "background" or os.getenv("BACKGROUND_JOBS") != "on":
        raise ValueError("scripted interview server requires its own background runner")
    module, name = args.provider.split(":", 1)
    if not module.startswith("e2e."):
        raise ValueError("provider factory must belong to the E2E package")
    from app.ai import set_ai_provider
    set_ai_provider(getattr(importlib.import_module(module), name)())
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
