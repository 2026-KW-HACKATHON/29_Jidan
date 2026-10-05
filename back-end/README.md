# Jidan API

Python 3.12 / FastAPI 기반 API.

## 데이터 설계

[Figma 기반 MVP ERD](docs/erd/README.md)는 인증·프로필·매장·초대·대타·관심 매장·매뉴얼·Q&A·알림의 관계와 확정 정책을 정리한 구현 전 문서입니다.

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

## DB 접근과 마이그레이션

SQLAlchemy 2.x(드라이버 PyMySQL)와 Alembic을 쓴다. 연결 정보는 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`에서 읽으며 엔진은 첫 사용 시 만든다.

- `app/db/`: 엔진·세션 팩토리(`get_engine`, `session_scope`), FastAPI 의존성 `get_session`, `UtcDateTime`, `new_uuid`.
  `session_scope`는 블록이 성공하면 commit, 예외가 나면 rollback한다. `get_session`은 같은 규칙을 요청 단위로 적용한다.
- 규칙: UUID는 `CHAR(36)`, 시각은 UTC `DATETIME(6)`(`UtcDateTime`은 타임존이 없는 값을 거부하고 읽을 때 UTC aware로 돌려준다), 근무일·요일은 `Asia/Seoul` 기준으로 서비스에서 계산한다. enum 값은 `VARCHAR` + `CHECK`다.
- `app/db/models.py`: P0 도메인 15개 테이블(users, 프로필, 매장·승인, 초대·접근, 공고·지원·근무 요청). 기준 스키마는 [ERD](docs/erd/README.md)에서 옮겼다.
- 기록을 보존해야 하는 참조에는 cascade 삭제를 두지 않는다. 다른 테이블을 봐야 하는 규칙(역할, 같은 매장 일치, 시간 중첩, 행 개수 상한, 30분 단위, 이메일 정규화)은 서비스 계층에서 검증한다.

```bash
cd back-end
export DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_local DB_USER=... DB_PASSWORD=...
python -m alembic upgrade head      # 최신 스키마 적용
python -m alembic downgrade -1      # 한 단계 되돌리기
python -m alembic current           # 현재 리비전
python -m alembic upgrade head --sql  # 연결 없이 SQL만 출력(오프라인)
python -m alembic revision --autogenerate -m "변경 설명"  # 새 리비전 초안, 반드시 직접 검토
```

**리비전은 한 줄(선형)로만 유지한다.** 새 리비전은 항상 현재 head 뒤에 붙이고, 한 시점에 한 사람만 추가한다. 병렬 PR로 head가 둘이 되면 `tests/test_migrations.py`가 실패한다. 발행된 리비전은 수정하지 않고 새 리비전을 추가한다. 모델과 마이그레이션이 어긋나면 같은 테스트가 실패한다.

### 테스트

기본 테스트는 테스트마다 마이그레이션으로 만든 SQLite 인메모리 DB를 쓰므로 DB 없이 `python -m pytest`가 통과한다. SQLite로 확인할 수 없는 MySQL 동작(잠금, 동시성, 실제 CHECK/생성 컬럼)은 `@pytest.mark.mysql`로 표시한다. `DB_*`가 없으면 건너뛰고, 설정되어 있어도 `DB_NAME`이 `_test`로 끝나지 않으면 건너뛴다(마이그레이션 테스트가 테이블을 지우기 때문이다). 스키마 테스트(`tests/test_schema.py`)는 SQLite와 MySQL 양쪽에서 같은 케이스를 실행한다.

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=jidan_test DB_USER=... DB_PASSWORD=... python -m pytest
```

Docker test 단계에는 MySQL이 없으므로 CI에서는 SQLite 테스트만 실행된다. 실제 MySQL 검증은 개발 서버에서 한다.

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
