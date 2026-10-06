# 격리 MySQL 검증과 실제 HTTP E2E

원격 PR #154(`back-end/feat/152-api-db-testing`)의 자동 검증 하네스를 로컬 구조에 맞게 옮긴 것이다. 운영·개발 서버와 공유 MySQL(`jidan-mysql`)을 쓰지 않는다. Docker Compose v2가 필요하며 호스트 Python·MySQL·Google 자격 증명은 필요하지 않다.

```bash
back-end/testing/run-e2e.sh
```

매번 고유한 `jidan-e2e-*` Compose 프로젝트와 tmpfs MySQL 8.4(`jidan_e2e_test`)를 새로 만든다. API와 DB는 호스트 포트를 열지 않는다. 순서는 다음과 같다.

1. MySQL을 시작한다.
2. `checks`: Ruff, 도구 시험(`testing/`), 그리고 `JIDAN_REQUIRE_MYSQL=1`로 전체 Python 시험(`tests/`, SQLite+MySQL)을 실행한다. 스키마를 지우고 다시 만드는 시험이 이 단계에서 끝난다.
3. 앞 단계가 성공한 뒤에야 `alembic upgrade head`와 실제 `app.main:app`(Uvicorn)을 시작한다. 스키마 초기화와 API 백그라운드 작업이 겹치지 않는다.
4. `e2e`: 실제 TCP HTTP로 가입·세션·CSRF·프로필 시험(`e2e/test_*_http.py`)을 실행하고 별도 DB 연결로 저장·거부·롤백 결과를 확인한다. 서버의 의존성·handler는 바꾸지 않는다. Google 신원·점주 계정 같은 초기 상태만 DB fixture로 만든다.
5. 단계마다 JUnit XML을 `testing/check_results.py`로 검사한다. 실패·오류·빈 리포트는 성공으로 치지 않는다. 도구·HTTP 시험은 skip/xfail도 실패다. 전체 Python 시험만 `--allow-skipped`로 의도된 skip(MySQL 전용 시험의 SQLite 변형, 대용량·실키 opt-in, 알려진 계약 차이의 strict xfail)을 허용한다. MySQL skip은 `JIDAN_REQUIRE_MYSQL=1`이 실행 중단으로 바꾸므로 여기에 숨지 않는다.

실패하면 비정상 종료하며 `.local/test-results/jidan-e2e-*/`에 `tooling.xml`, `python.xml`, `http-e2e.xml`, `services.log`를 남긴다(Git에 추가하지 않는다). 정리 때 그 실행이 만든 이미지도 지운다(실행마다 프로젝트 이름이 달라 남기면 쌓인다). 정리 실패도 성공으로 치지 않는다. Ctrl+C·TERM은 실행 중인 Docker 명령을 멈추고 정리한다. 강제 종료로 남은 프로젝트는 출력된 이름으로 정리한다.

```bash
docker compose -p <남은-jidan-e2e-프로젝트> -f back-end/testing/compose.yml --profile e2e down --volumes --rmi local
```

로컬에서 2단계를 일부 시험으로 줄이려면 `JIDAN_CHECKS_PYTEST_ARGS="tests/test_health.py tests/test_request_id.py"`처럼 경로를 준다(CI는 비워 전체를 돈다).

## 실키 AI와 외부 연동

API 컨테이너는 `AI_PROVIDER=fake`이고 키를 받지 않는다. `checks`는 `-m "not openai"`로 실키 시험을 제외한다. 실키 시험은 지금처럼 `JIDAN_RUN_OPENAI=1`과 키를 준 개발자 PC에서만 opt-in으로 실행한다. 외부 Google·Kakao, 배포 프록시·운영 이미지, 브라우저 흐름은 이 suite의 범위가 아니다.

## 로컬 구조와의 관계

- `e2e/`의 기존 시연 하네스(`demo_scenario`, `serve`, `browser_login` 등)는 그대로다. HTTP 시험은 같은 패키지의 `conftest.py`와 `test_*_http.py`로 추가됐고 `pyproject.toml`의 `testpaths = ["tests"]` 때문에 기본 `pytest`에는 들어가지 않는다.
- 원격의 수동 sandbox(`testing.sandbox`, `/sandbox/login/*`)는 가져오지 않았다. 로그인 fixture를 서버 경로로 여는 도구라서, 로컬은 이미 있는 `e2e.serve`·`e2e.browser_login`(로컬 DB·키 가드)을 쓴다. `e2e/conftest.py`는 `/sandbox` 경로가 404인지 계속 확인한다.
- 원격 시험 중 두 곳만 로컬 설계에 맞게 고쳤다. 로컬 프로필 저장은 자식 행을 위치별로 재사용해 gap lock 교착을 피하므로(`app.worker_profile.rewrite`), 이전 행 ID가 모두 사라진다고 단언하던 부분을 "새 목록 밖의 행과 이전 요일이 남지 않음"으로 바꿨다.
- 운영 이미지에는 `tests/`, `e2e/`, `testing/`이 들어가지 않는다(`deploy/backend/Dockerfile`의 runtime-source 단계).

GitHub Actions `backend CI/CD`는 GitHub-hosted `Backend HTTP E2E`(`.github/workflows/backend-e2e.yml`) 성공 뒤에 기존 이미지 빌드·배포 단계를 진행한다. 리포트와 로그는 artifact로 7일 보관한다. 기존 RPi4 test 단계는 여전히 SQLite로만 돈다.
