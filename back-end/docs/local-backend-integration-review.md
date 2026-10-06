# 로컬 백엔드 통합 PR 비교·검증

이 브랜치는 `2026-KW-HACKATHON/29_Jidan`의 `back-end/dev` `5fd6896955ca9133c5b8967485438dd86eceefc4`를 기준으로, 검증된 로컬 통합 커밋 `dbba4de1fa8b57f3d330d1e7b1b9498a841707f8`의 백엔드와 필요한 운영 변경을 추출했다. 원본/통합 작업공간은 보존하고 프론트 파일은 포함하지 않는다. 백엔드 실행 코드·테스트·마이그레이션·의존성과 선정한 배포/CI 파일은 검증 소스와 byte 단위로 동일하다. 이 문서만 비교·검증 요약으로 추가한다.

## 원격 대비

| 항목 | 원격 back-end/dev | 이 PR |
| --- | --- | --- |
| OpenAPI operation | 102 선언 | 102 선언 |
| 실제 연결 업무 operation | 18 | 102 |
| ORM 테이블 | 19 | 50 |
| migration head | 0006 | 0041 |
| 관리자 hash | PBKDF2_SHA256 | PBKDF2_SHA256 + 기존 scrypt |
| 응답 선언 | 0.10.0 | 0.10.1, 원격 선언 전부 보존 + 승인된 25개 추가 |

추가 구현은 매장 접근·초대·근무자 관리, 대타 공고·지원·요청, 홈/캘린더/관심 매장, 알림, 매뉴얼 미디어/인터뷰/정정/게시와 근무자 AI 질문을 포함한다. 기존 OAuth 구현을 기반으로 계정/CSRF/트랜잭션 경합 보호를 보강한다.

기존 `0001..0006` version 파일은 바꾸지 않고 12개 migration을 추가한다. API 요청·인증·성공 응답·schema 행동은 승인 원격과 같으며, `tests/test_approved_contract.py`가 102개 operation을 검사한다. 정기·대타 접근 공존, 수락 경로별 동일 정책, 정지 근무자 집계 제외는 사용자 합의를 반영한다.

## 라우트·시험 재배치

원격의 `app/owner_stores.py`·`app/store_summary.py`는 `app/stores.py` 한 router로 통합한다. 점주 매장 목록/상세/등록/요약 네 경로와 요청·응답 구조는 유지한다. 매장 생성 중 owner 상태 재확인, 멱등성 재시도 시 현재 권한 재검사와 관리 요약의 ACTIVE WORKER 집계를 보강했다.

삭제되는 파일은 위 모듈 2개와 기존 시험/helper 9개다. 옛 모듈 내부에 결합된 시험 책임은 `test_owner_stores.py`, `test_store_approvals.py`, `test_store_adoption.py`, `test_admin_password_config.py`, `test_admin_password_compat.py`로 이어진다. 리뷰 시 삭제 파일과 대체 시험을 함께 비교한다.

## 실행 결과 및 소스 연결

| 검사 | 결과 |
| --- | --- |
| 전체 pytest (SQLite + 독립 MySQL) | 6,248 passed / 17 skipped / 19 deselected / failure·error 0 |
| 대용량 MySQL opt-in 2개 추가 검사 | 2 passed / 0 skipped |
| MySQL 전체 + 위 추가 검사 | 2,326 passed, 미검증 0 |
| 실제 HTTP 하네스 | 148 passed / 0 skipped |
| 원격 PBKDF2를 설정한 실제 API 데모 | 33/33 단계 통과 |
| 하네스 도구 | 35 passed |
| 문서·OpenAPI 검사 | 451 passed |
| 최종 배포 hash 호환 관련 검사 | 6 passed |
| runtime env 호스트 검사 | 10 passed |

원 실행의 17 skip 중 대용량 MySQL 2개는 후속 opt-in 실행으로 닫았다. 남은 15개는 SQLite에서 지원하지 않는 MySQL 코드/잠금 동작이며 동일 이름의 MySQL 대응 case가 모두 통과했다. 유료 모델 opt-in 19개는 이번 전체 회귀에서 제외했고, 기존 실제 모델 검증 9개와 별도로 구분한다. 현재 실행코드가 해당 검증 후 바뀌지 않았으므로 변경 없이 전체 검사를 다시 반복하지 않았다.

실제 HTTP 데모는 독립 MySQL과 원격 PBKDF2 관리자 설정으로 가입→승인→초대함/메일 링크→대타/만료→알림→매뉴얼 미디어/전사/인터뷰/정정/게시→질문과 종료 접근을 검사했다. 이번 데모의 AI/STT는 Fake, 메일은 로컬 SMTP, 주소 검증 네트워크 경계는 Fake다. 실제 외부 Google OAuth·실수신 메일·운영 서버 배포를 검증한 결과가 아니다. 기존 실제 마이크 녹음/업로드와 실제 모델 검증 근거는 각각 별도다.

Redocly 경고 14개는 남아 있다. 대상 component 예시 4개는 YAML 1.2로 읽어 별도 Draft202012Validator로 검사하면 모두 유효하다. 경고에 맞춰 승인 schema를 임의 변경하지 않았다. Git diff의 기존 EOF 빈 줄 5개도 검증 소스 그대로 보존했다.

재현: `back-end/testing/run-e2e.sh`. 대용량은 독립 `*_test` MySQL에서 `JIDAN_LARGE_CONTENT=1 JIDAN_REQUIRE_MYSQL=1 python -m pytest tests/test_manual_large_content.py`를 추가 실행한다. 실제 모델은 별도 키·비용 상한을 정한 opt-in으로만 실행한다.

## 중점 리뷰·병합 계획

- 열려 있는 PR #154는 같은 base이며 HTTP/MySQL 하네스·Auth/Profile E2E가 겹친다. 이 PR은 그 하네스를 로컬 구조에 맞게 선별 이식했고 중복 sandbox 로그인은 포함하지 않는다. 두 PR의 반영 순서와 중복 파일 소유를 팀과 정하고, 합친 tree로 관련 CI를 다시 검증해야 한다. #154를 자동 종료하거나 대신 병합하지 않는다.
- 0006→0041 DB 변경은 적용 전 백업 및 migration 기록을 확인한다. MySQL DDL은 비원자적이며 code rollback만으로 DB를 되돌렸다고 간주하지 않는다. 운영 DB downgrade/복구는 팀과 별도 계획한다.
- 원격 관리자 PBKDF2 설정을 그대로 사용하며 새 기본 CLI도 같은 형식이다. 기존 scrypt를 강제 회전하지 않는다. 로그인/health/배포 gate가 동일 parser를 사용한다.
- PR CI의 `deploy` 값은 false다. 현재 요청 범위는 branch upload와 비교 PR이며 병합·운영 배포·외부 데모 공개는 하지 않는다.

관련 이슈: Refs #126, #127, #128, #122, #152.
