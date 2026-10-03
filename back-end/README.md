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

## API 설계

[Figma 기반 OpenAPI 명세](openapi.yaml)를 제공합니다.
구현 전 계약이며 실제 인증·프로필 관리·승인 endpoint는 아직 제공하지 않습니다.

- [인증 화면 근거·인가 정책](docs/auth-design.md)
- [관리자 매장 승인 계약](docs/store-approval-design.md)
- [일반회원 프로필 조회·기본 정보·근무 정보·가능 시간 수정 계약](docs/worker-profile-design.md)

```bash
cd docs
npm ci
npm run check
npm run dev
```

[로컬 Swagger 문서](http://127.0.0.1:5500)를 확인합니다. 자세한 실행 방법은 [문서 서버 안내](docs/README.md)를 참고합니다.
