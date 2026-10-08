"""Serve the CI-built API contract only in the development deployment."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.staticfiles import StaticFiles

ARTIFACT_DIRECTORY = Path(__file__).resolve().parent.parent / "swagger-static"
PUBLIC_FILES = frozenset({
    "index.html", "init.js", "openapi.yaml", "openapi.json", "build-info.json",
    "swagger-ui.css", "swagger-ui-bundle.js", "swagger-ui-standalone-preset.js",
})


class DesignDocsFiles(StaticFiles):
    async def get_response(self, path, scope):
        if path not in PUBLIC_FILES and path not in {"", "."}:
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def install_design_docs(
    app: FastAPI, *, environment: str | None = None, directory: Path | None = None,
) -> None:
    environment = os.getenv("APP_ENV", "local") if environment is None else environment
    if environment != "dev":
        return
    directory = ARTIFACT_DIRECTORY if directory is None else directory
    if not all((directory / name).is_file() for name in PUBLIC_FILES):
        raise RuntimeError("Development Swagger artifact is missing or incomplete")
    app.mount("/api/swagger", DesignDocsFiles(directory=directory, html=True), name="design-swagger")
