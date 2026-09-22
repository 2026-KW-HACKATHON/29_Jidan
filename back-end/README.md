# Jidan API

Python 3.12 / FastAPI 기반 API.

```bash
cd back-end
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
```

검증:

```bash
python -m ruff check .
python -m pytest
```

`GET /api/health`는 인증 없이 상태를 반환한다. 실행·배포 계약은
[CI/CD 운영 문서](../deploy/CI-CD.md)를 참고한다.

로컬에서 DB 설정이 없으면 `database: not_configured`를 반환한다. `APP_ENV=dev|production`에서는 DB 설정이 필수이며, `/api/health`가 `SELECT 1`까지 확인한다. DB 장애 시 자격 증명을 노출하지 않고 503을 반환한다.
