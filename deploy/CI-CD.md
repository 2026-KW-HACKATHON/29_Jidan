# CI/CD 운영

## 실행 구조

- **RPi4**: `jidan-rpi4-build` self-hosted runner에서 테스트와 ARM64 이미지 빌드, GHCR 업로드.
- **RPi5**: `jidan-rpi5-deploy` self-hosted runner에서 이미지를 내려받아 Docker Compose 배포.
- 배포 서버는 소스를 빌드하지 않는다. 이미지는 커밋 SHA로 게시하고 실제 배포는 digest로 고정한다.
- FE와 BE는 독립적인 Compose 프로젝트로 배포한다. 한쪽 배포가 다른 쪽 이미지를 바꾸지 않는다.

| 브랜치 | 환경 | 도메인 | 배포 대상 |
| --- | --- | --- | --- |
| `front-end/dev` | `dev` | `dev-jidan.leehyowon14.dev` | FE |
| `back-end/dev` | `dev` | `dev-jidan.leehyowon14.dev` | BE |
| `main` | `production` | `jidan.leehyowon14.dev` | 변경된 FE/BE |

PR은 테스트·빌드만 수행한다. 같은 저장소의 PR만 self-hosted runner에서 실행하며, fork PR은 자동 실행하지 않는다. `feat/fix/hotfix → dev → main` 병합 규칙은 [BRANCHING.md](../BRANCHING.md)를 따른다.

두 앱은 별도로 배포되므로 `main`의 FE·BE 배포는 원자적이지 않다. API 변경은 기존 클라이언트와 호환되도록 준비한다.

## 앱 초기화 시 필요한 파일

애플리케이션 코드는 별도 작업에서 초기화한다. 해당 디렉터리가 없으면 워크플로우는 배포 로직 테스트만 수행하고 앱 빌드·배포를 생략했다고 실행 요약에 기록한다. 디렉터리가 생성된 이후에는 아래 계약이 충족되지 않으면 실패한다.

### React + Vite

`front-end/`에 다음을 준비한다.

- `package.json`, `package-lock.json`을 커밋한다. 빌드 환경은 Node.js 22다.
- `npm run lint`, `npm run test:ci`, `npm run build`를 제공한다.
- `test:ci`는 감시 모드 없이 종료해야 한다. Vitest를 사용한다면 `vitest run`으로 설정한다.
- Vite 빌드 결과는 `dist/`로 출력한다.
- API 기본 경로는 `/api`다. 빌드 시 `VITE_API_BASE_URL=/api`를 제공한다.
- `VITE_*` 값은 브라우저에 노출되므로 비밀 값을 넣지 않는다.
- 로컬 개발은 Vite의 `/api` 프록시를 `http://127.0.0.1:8000`에 연결한다.

운영 컨테이너는 Nginx의 8080 포트에서 정적 파일을 제공한다. SPA 경로는 `index.html`로 fallback하며, 없는 `/assets/*` 파일은 404를 반환한다.

### FastAPI

`back-end/`에 다음을 준비한다.

- `requirements.txt`: FastAPI, Uvicorn 및 런타임 의존성을 명시한다.
- `requirements-dev.txt`: Ruff, pytest 및 테스트 의존성을 명시한다.
- 재현성을 위해 의존성 버전을 고정한다. 빌드 환경은 Python 3.12다.
- 진입점은 `app/main.py`의 `app`이다. `python -m uvicorn app.main:app`으로 실행한다.
- `python -m ruff check .`, `python -m pytest`가 통과해야 한다. 테스트가 없는 상태도 실패한다.
- 인증 없이 `GET /api/health`가 200을 반환해야 한다. 앱 초기화 때 해당 endpoint와 테스트를 추가한다.
- DB 설정은 `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` 환경 변수에서 읽는다.
- 환경 구분은 `APP_ENV=dev|production`을 사용한다.

### DB 마이그레이션

도구는 Alembic이며 리비전은 `back-end/migrations/versions/`에 있고 이미지에 함께 들어간다.

**dev 백엔드는 배포 시 자동 적용한다.** `deploy.sh`는 `dev/backend`일 때만 새 이미지 pull 직후, `pending` 기록과 컨테이너 교체 전에 새 릴리즈의 Compose 정의로 일회성 컨테이너를 실행한다.

```bash
docker compose run --rm --no-deps -T --name <프로젝트>-migrate-<릴리즈> backend python -m alembic upgrade head
```

- 같은 이미지·`runtime.env`·`shared-mysql_default` 네트워크를 쓰며 포트는 열지 않는다. 대상 DB는 `jidan_dev`다.
- 실패하면 배포를 즉시 중단한다. 실행 중인 이전 릴리즈와 `current`는 그대로 두고 새 컨테이너를 띄우지 않는다. 트랜잭션 DDL이 없는 MySQL 특성상 일부 리비전만 적용됐을 수 있으므로 `alembic current`와 실제 테이블을 확인해 정리한 뒤 다시 배포한다.
- 실행 전에 컨테이너 이름을 권한 `600`의 `migration.pending`에 원자적으로 기록한다. SIGINT·SIGTERM으로 중단되면 실행 중인 명령과 일회성 컨테이너를 정리하고 이전 릴리즈를 유지한다. SIGKILL로 즉시 정리할 수 없으면 다음 배포가 같은 `deploy.lock`을 확보한 뒤 기록된 컨테이너부터 제거한다.
- Docker 조회·제거 실패 또는 제거 후 컨테이너가 남아 있으면 `migration.pending`을 유지하고 새 마이그레이션·배포를 차단한다. 빈 조회는 생성 요청이 아직 처리 중일 수 있으므로 1초 간격으로 최대 10회 재조회한다. 끝까지 이름이 보이지 않아 생성·완료 여부가 불확실하면 기록을 지우지 않고 배포를 차단한다. `compose run --rm`이 성공을 반환해 실행 완료가 확인된 경우에만 기록을 직접 정리한다.
- 불확실한 기록이 남으면 운영자가 원래 Compose 클라이언트의 종료, Docker의 해당 생성 요청 완료, 남은 컨테이너 정리와 부분 DDL 적용 상태를 확인한 뒤 기록을 수동으로 정리한다. 빈 조회만으로 기록을 삭제하지 않는다. 이미 끝난 컨테이너도 클라이언트 성공 확인 전에 강제 종료됐다면 이 보수적인 복구 절차가 필요하다. 이 복구는 컨테이너 교체용 `pending`과 별도로 처리한다.
- 마이그레이션 출력은 Actions 로그에 남는다. 연결 정보는 `DB_*`에서 읽어 출력하지 않으며, 실패한 SQL의 바인딩 값도 출력하지 않는다(`hide_parameters=True`).

**production과 frontend는 자동 실행하지 않는다.** production은 dev에서 적용·검증된 리비전을 포함한 **새 이미지**를 일회성 컨테이너로 실행해 수동 적용한다. 현재 실행 중인 backend 이미지는 새 리비전을 포함하지 않을 수 있으므로 배포 전 `compose exec backend ... upgrade head`의 대상으로 사용하지 않는다.

1. CI가 GHCR에 게시했고 dev에서 검증한 새 backend 이미지 digest를 선택한다. 그 이미지의 리비전이 운영에 배포할 릴리즈와 동일하고 이전 코드와 호환되는지 확인한다(아래 정책). production CI/CD를 시작하기 전에 RPi5의 해당 소스 checkout에서 다음을 실행한다. `image`의 placeholder는 검증한 실제 digest로 바꾼다.

   ```bash
   (
     set -euo pipefail
     umask 077
     image='ghcr.io/2026-kw-hackathon/29_jidan-backend@sha256:<검증한 새 이미지 digest>'
     root=/home/ubuntu/apps/jidan/production/backend
     exec 9>"$root/deploy.lock"
     flock -w 300 9
     migration_release=$(mktemp -d "$root/releases/migration.XXXXXXXX")
     cp deploy/backend/compose.yml "$migration_release/compose.yml"
     install -m 600 "$root/runtime.env" "$migration_release/runtime.env"
     printf 'IMAGE_REF=%s\nAPP_PORT=3023\n' "$image" > "$migration_release/.env"
     python3 deploy/scripts/check_runtime_env.py production "$migration_release/runtime.env"
     compose=(docker compose -p jidan-production-backend
       --env-file "$migration_release/.env" -f "$migration_release/compose.yml")
     "${compose[@]}" config --quiet
     "${compose[@]}" pull backend
     "${compose[@]}" run --rm --no-deps -T \
       --name "jidan-production-backend-migrate-${migration_release##*.}" \
       backend python -m alembic upgrade head
     "${compose[@]}" run --rm --no-deps -T backend python -m alembic current
     "${compose[@]}" run --rm --no-deps -T backend python -m alembic heads
     rm -rf -- "$migration_release"
   )
   ```

   일회성 컨테이너에는 production `runtime.env`의 `DB_*`가 주입된다. 포트를 열지 않고 `shared-mysql_default`로 production DB에만 연결한다. 실행 중인 앱과 `current`는 바꾸지 않으며 자격 증명은 출력하지 않는다.
2. 새 이미지에서 조회한 `current`와 `heads`가 같은 리비전인지 확인한 뒤 production CI/CD로 앱을 배포한다. 배포 후 `GET /api/health`의 `database: ok`와 새 이미지 적용을 확인한다.
3. 실패·중단 시 일부만 적용될 수 있다. 새 배포를 진행하지 말고 이름으로 남은 일회성 컨테이너를 확인·정리한다. 새 이미지로 `alembic current`와 실제 테이블을 확인한 뒤 수동으로 복구한다. 실패 시 남은 `migration.*` 디렉터리에는 비공개 환경 파일 사본이 있으므로 복구 완료 후 삭제한다.

**expand-only 정책과 롤백 한계**: 이미지 롤백(배포 스크립트의 자동 복구, 커밋 revert 후 재배포)은 DB 스키마를 되돌리지 않는다. dev는 마이그레이션이 성공한 뒤 새 컨테이너 시작·헬스체크가 실패하면 이전 이미지가 새 스키마에서 다시 실행된다. 따라서 리비전은 이전 버전 코드와 호환되는 변경(테이블·nullable 또는 기본값 있는 컬럼·인덱스 추가)만 한다. 컬럼·테이블 삭제, 이름 변경, 타입 축소 같은 비호환 변경은 추가 → 코드 전환 → 제거의 별도 릴리즈로 나눈다. 스키마를 되돌려야 하면 이전 이미지로 복구하기 전에 `alembic downgrade -1`을 수동으로 실행하며, 데이터를 삭제하는 downgrade는 사전에 백업한 뒤에만 수행한다.

## RPi5 네트워크 및 DB

Cloudflare Tunnel → Nginx 80 → 환경별 localhost 포트로 전달한다. `/api`와 `/api/*`는 접두사를 제거하지 않고 BE로 전달하며, 나머지는 FE로 전달한다.

| 환경 | FE 포트 | BE 포트 | MySQL DB / 계정 |
| --- | --- | --- | --- |
| dev | `127.0.0.1:3020` | `127.0.0.1:3021` | `jidan_dev` |
| production | `127.0.0.1:3022` | `127.0.0.1:3023` | `jidan_production` |

- DB 컨테이너: 기존 `shared-mysql`.
- Docker 네트워크: 기존 `shared-mysql_default`.
- BE 내부 접속 주소: `shared-mysql:3306`.
- 각 계정은 해당 DB에만 권한을 가진다.
- 서버 환경 파일: `/home/ubuntu/apps/jidan/<환경>/backend/runtime.env` (권한 600).
- 환경 파일과 자격 증명은 Git이나 Actions 로그에 출력하지 않는다.

Nginx 설정 원본은 `deploy/nginx/`에 있고, 서버에서는 `/etc/nginx/sites-available/jidan-dev`, `jidan-production`을 사용한다. 프록시 변경은 앱 배포와 별도로 `nginx -t` 후 reload한다.

## backend Origin 환경 설정

비공개 `backend/runtime.env`에는 아래 값을 **따옴표 없이 한 번만** 설정한다.

| 환경 | 설정 |
| --- | --- |
| dev | `ALLOWED_ORIGINS=https://dev-jidan.leehyowon14.dev` |
| production | `ALLOWED_ORIGINS=https://jidan.leehyowon14.dev` |

Compose의 `env_file`은 `format: raw`로 값을 주입한다. 와일드카드, 두 환경의 도메인 혼합, localhost 추가, 따옴표, 변수 치환, 중복 키를 사용하지 않는다. 프론트엔드는 각 환경의 동일 출처 `/api`를 유지한다. 설정 변경 시 다른 값은 유지하고 파일 권한은 `600`으로 보존한다. 파일 내용 전체나 다른 환경 변수, 세션·CSRF 토큰을 Git/Actions/이슈에 출력하지 않는다.

배포 서버에서 파일을 수정한 뒤, 해당 배포 커밋의 검사기로 컨테이너 변경 없이 사전 점검한다. 성공 시 출력이 없고 실패 시 값이 포함되지 않은 안내와 종료 코드 `2`를 반환한다.

```bash
python3 deploy/scripts/check_runtime_env.py dev /home/ubuntu/apps/jidan/dev/backend/runtime.env
python3 deploy/scripts/check_runtime_env.py production /home/ubuntu/apps/jidan/production/backend/runtime.env
```

배포 스크립트는 권한 `600`으로 복사한 릴리즈의 환경 파일을 이미지 pull 전에 검사한다. 누락·빈 값·다른 Origin은 배포를 중단한다. 컨테이너 시작 뒤에는 `ALLOWED_ORIGINS`만 검사하며 값은 출력하지 않는다. 주입된 값이 환경과 다르면 `current`를 확정하지 않고 기존 롤백 절차를 따른다. frontend 배포에는 이 검사를 적용하지 않는다.

파일 수정만으로 실행 컨테이너의 환경 변수는 바뀌지 않는다. 적용은 기존 backend GitHub Actions에서 `back-end/dev` 또는 `main`의 Run workflow로 수행하며 서버에서 직접 Compose를 재배포하지 않는다. PR은 테스트·빌드만 수행하므로 검사 코드도 각 배포 브랜치에 병합되어야 적용된다.

배포 성공 후 환경 파일과 릴리즈 사본의 권한 `600`, 컨테이너의 환경별 단일 Origin, 공개 `/api/health`를 확인한다. 인증 구현이 포함된 환경에서는 유효한 세션·CSRF 토큰과 자신의 Origin이 통과하고, 반대 환경·임의·누락 Origin 및 누락·오류 토큰이 `403 CSRF_INVALID`인지 확인한다. 인증 코드가 아직 없는 production 이미지에서는 설정 주입과 헬스체크만 검증 가능하며, 정상 인증 릴리즈 후 요청 검증을 별도로 수행한다. 실제 OAuth·가입→홈→로그아웃 E2E는 사용자 검증 결과와 함께 기록하고 자동 테스트로 대체했다고 보고하지 않는다.

검증 명령(Linux):

```bash
bash -n deploy/scripts/deploy.sh
python3 -m unittest discover -s deploy/tests -v
```

설정 복구도 비공개 파일을 권한 `600`으로 수정한 뒤 GitHub Actions로 적용한다. 이전 릴리즈 사본에 값이 누락되어 있으면 이미지 롤백만으로 설정 문제가 다시 나타날 수 있다. 설정 복구와 이전 코드의 인증 지원 여부를 함께 확인한다.

## 배포 및 복구

1. RPi4에서 테스트 단계와 런타임 이미지를 각각 빌드한다.
2. 저장소의 `GITHUB_TOKEN`으로 GHCR에 게시한다. 별도의 레지스트리 비밀번호는 필요하지 않다.
3. RPi5에서 이미지 digest와 환경별 설정으로 새 릴리즈 디렉터리를 만든다.
4. 이미지를 pull한다. dev 백엔드는 이때 [DB 마이그레이션](#db-마이그레이션)을 적용하고, 실패하면 컨테이너를 바꾸지 않고 중단한다. 이후 `docker compose up -d --wait`로 시작한다.
5. 실행 이미지 ID, 로컬 헬스체크, 공개 도메인 응답을 확인한다.
6. 성공하면 `current` 심볼릭 링크를 새 릴리즈로 전환한다.
7. 오류·SIGINT·SIGTERM 종료 시 실행 중인 배포 명령을 멈추고 이전 Compose·이미지·환경 파일로 복구를 시도한다. 첫 배포라 이전 릴리즈가 없다면 실패한 컴포넌트를 내린다.
8. 컨테이너 변경 전에 `pending` 기록을 남긴다. SIGKILL·전원 차단처럼 즉시 복구할 수 없는 경우에는 다음 배포 실행 시 먼저 복구한다. 복구 실패 시 기록을 유지하며 새 배포를 진행하지 않는다. Actions가 복구 도중 프로세스를 강제 종료하는 경우도 같은 절차를 따른다.

릴리즈는 `/home/ubuntu/apps/jidan/<환경>/<frontend|backend>/releases/`에 보관한다. 백엔드 릴리즈에는 환경 파일 사본이 있으므로 해당 디렉터리도 비공개로 관리한다. 성공한 배포 뒤 해당 환경·컴포넌트의 검증된 최근 릴리즈 5개와 현재 릴리즈를 보관하고 나머지를 정리한다. 복구 중인 `pending` 기록이 있으면 릴리즈를 삭제하지 않는다.

RPi4는 `jidan-ci` 전용 Buildx builder를 사용한다. 테스트 단계는 Docker 이미지로 내보내지 않고 전용 캐시에만 저장한다. 빌드 종료 시 해당 캐시를 최대 4GB·여유 공간 8GB 목표로 정리한다(정리 가능한 캐시에 한하므로 용량을 보장하는 quota는 아니다). FE/BE별 최근 이미지 참조 5개를 보관하며, 실행·정지된 컨테이너가 사용하는 이미지는 제외한다. RPi5에서는 보관 중인 모든 환경의 릴리즈 이미지도 보호한다. 다른 프로젝트의 이미지·builder와 Docker volume은 정리하지 않으며 `--force` 이미지 삭제를 사용하지 않는다. GHCR 원격 이미지에는 이 로컬 보관 정책을 적용하지 않는다.

정리 단계가 실패하면 CI에 실패가 표시된다. 앱 배포 후의 정리 실패는 경고를 남기며, 이미 검증·확정된 앱 배포를 롤백하지 않는다.

수동 재배포는 GitHub Actions에서 해당 frontend/backend 워크플로우의 Run workflow를 실행하고 `main` 또는 해당 `dev` 브랜치를 선택한다. 다른 브랜치는 배포하지 않는다.

## 개발 Swagger 자동 배포

Swagger는 별도 수동 배포 대상이 아니라 개발 백엔드 이미지에 포함된 문서입니다.

1. PR: backend CI에서 Node 명세 lint/계약 테스트/정적 빌드, Python 검사/테스트, ARM64 이미지 빌드를 수행합니다. PR에서는 CD가 실행되지 않습니다.
2. `back-end/dev` 반영: 같은 CI를 통과한 이미지를 GHCR에 게시하고 기존 RPi5 CD가 개발 backend Compose를 교체합니다.
3. 개발 앱은 `APP_ENV=dev`에서만 `/api/swagger/`를 제공합니다. 동일 이미지가 production에서 실행되어도 설계 Swagger 경로는 등록하지 않습니다.
4. CD는 기존 health 및 이미지 digest 확인에 더해 로컬/공개 Swagger UI와 JSON을 검증합니다. 실패하면 기존 복구 절차로 이전 backend 이미지에 돌아갑니다.

문서 주소는 `https://dev-jidan.leehyowon14.dev/api/swagger/`입니다. 같은 경로 아래 `openapi.json`, `openapi.yaml`, `build-info.json`을 제공합니다. API server base URL은 `https://dev-jidan.leehyowon14.dev`이며 각 명세 path에 `/api`가 이미 포함됩니다. `/api` 접두사는 두 번 붙이지 않습니다.

Nginx 변경·신규 포트·별도 문서 컨테이너는 필요하지 않습니다. 정적 문서는 Docker Node 단계에서 생성하며 런타임 이미지에는 Node 의존성을 설치하지 않습니다. `build-info.json`은 문서 버전, GitHub 커밋 revision, operation 수, 명세 해시를 제공하고 산출물은 Git에 추가하지 않습니다. 기존 로컬 5500 검수 서버는 계속 사용합니다.

## 초기 구성 검증 (2026-09-21)

- 두 runner의 GitHub online 상태 확인.
- RPi4에서 배포 스크립트 테스트 6개 통과: 성공, 잘못된 입력, pull 실패, 시작 실패, 이미지 불일치, 공개 URL 실패.
- actionlint 및 RPi5 Docker Compose 설정 검증 통과.
- 개발·운영 DB/계정 및 비공개 환경 파일 생성.
- RPi5 Nginx 설정 검증·reload 완료. 두 공개 도메인의 요청이 RPi5 Nginx에 도달하는 것 확인.
- 앱 초기화 전이므로 실제 React/FastAPI 이미지 빌드·GHCR 업로드·앱 배포는 미검증. 현재 앱 포트에 실행 중인 서비스가 없어 공개 도메인은 502를 반환한다.

검증 명령:

```bash
# Linux / RPi4
bash -n deploy/scripts/deploy.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/tests -v

# actionlint 설치 환경
actionlint -shellcheck= -pyflakes= .github/workflows/*.yml
```

## OAuth callback 프록시 검증

backend CI는 `python3 deploy/scripts/verify_nginx.py`로 dev/production 원본 설정을
일회용 Nginx 컨테이너에서 `nginx -t` 검증한다. 임의의 localhost 포트를 사용하며
정상 upstream(200)과 연결 실패(502) callback 모두에서 가짜 code/state가 로그에
남지 않는지 확인한다. 일반 요청의 로그가 실제로 남는지도 대조한다.
이 검사는 서버 설정을 교체하거나 reload하지 않는다. 실제 서버의 설정 반영과
`nginx -t`/reload 및 로그 차단 확인은 배포 시 별도로 수행해야 한다.
