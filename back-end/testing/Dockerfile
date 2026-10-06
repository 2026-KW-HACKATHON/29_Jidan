FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY app ./app
COPY migrations ./migrations
COPY tests ./tests
COPY testing ./testing
COPY e2e ./e2e
COPY alembic.ini pyproject.toml openapi.yaml ./
