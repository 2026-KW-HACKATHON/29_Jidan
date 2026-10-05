FROM node:22-bookworm-slim AS swagger
WORKDIR /build/docs
COPY docs/package.json docs/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY docs/ ./
COPY openapi.yaml /build/openapi.yaml
ARG DOCS_REVISION=uncommitted
RUN npm run check && DOCS_REVISION="$DOCS_REVISION" npm run build -- /swagger-static

FROM python:3.12-slim AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM dependencies AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
COPY --from=swagger /swagger-static ./swagger-static
RUN python -m ruff check . && python -m pytest

FROM dependencies AS runtime
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . .
COPY --from=swagger --chown=app:app /swagger-static ./swagger-static
USER app
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=24 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
