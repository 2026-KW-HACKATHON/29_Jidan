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
구현 전 계약이며 실제 인증·프로필·매장·초대·근무자 관리 endpoint는 아직 제공하지 않습니다.

- [인증 화면 근거·인가 정책](docs/auth-design.md)
- [관리자 매장 승인 계약](docs/store-approval-design.md)
- [일반회원 프로필 조회·기본 정보·근무 정보·가능 시간 수정 계약](docs/worker-profile-design.md)
- [점주 관리 매장 조회·추가·현황 요약](docs/owner-store-design.md)
- [근무자 초대·재전송·취소·수락·거절](docs/store-invitation-design.md)
- [근무자 조회·자료 접근 기간·수동 종료](docs/store-worker-design.md)
- [매뉴얼·AI 인터뷰·음성/사진·점주 확인 발행·근무자 열람](docs/manual-interview-design.md)

```bash
cd docs
npm ci
npm run check
npm run dev
```

[로컬 Swagger 문서](http://127.0.0.1:5500)를 확인합니다. 자세한 실행 방법은 [문서 서버 안내](docs/README.md)를 참고합니다.

설계 Swagger는 `back-end/dev`의 CI/CD를 통해 개발 환경에서만 `/api/swagger/`로 제공됩니다. [주소·자동 배포 흐름](docs/README.md#개발-서버-cicd와-endpoint)을 참고합니다. PR 단계에서는 배포하지 않으며 업무 API 구현과 설계 문서 제공은 별개입니다.
