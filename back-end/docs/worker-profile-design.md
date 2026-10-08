# 일반회원 프로필 API 설계

[Figma Design](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1)의 일반회원 화면에 따른 API 계약이다. [OpenAPI 원본](../openapi.yaml)과 [로컬 Swagger](http://127.0.0.1:5500)에서 확인한다. #106에서 아래 API 4개와 DB 처리를 구현했다.

## 내 프로필 조회

`GET /api/users/me/profile`

근거 화면: [일반회원 / 내 프로필 (255:695)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=255-695).

이름·Google 이메일·전화번호·생년월일·성별·신입/경력·경력 목록·가능 시간을 한 번에 반환한다. Google 이메일은 읽기 전용이다. ID·WORKER 역할·최종 변경 시각은 서버에서 결정한다. 가능 시간은 Asia/Seoul 기준 매주 반복이며 주당 시간 표시는 요일별 기간을 합산한다.

- 가입 완료된 ACTIVE WORKER의 회원 세션만 허용한다. 대상 회원 ID는 세션에서 찾는다.
- 가입 세션만 존재하면 401 REGISTRATION_REQUIRED, 세션 없음/만료/폐기는 401 SESSION_EXPIRED다.
- OWNER 등 다른 역할은 403 FORBIDDEN, 정지 계정은 403 ACCOUNT_SUSPENDED다.
- 관리자 승인 API의 body password 인증은 사용하지 않는다.
- 응답은 Cache-Control: no-store다. 경력/가능 시간은 저장 순서로 반환한다.

## 화면 근거와 설계 제안

세 영역의 수정 버튼과 읽기 전용 Google 이메일은 Figma에서 확인했다. API 경로·세션/CSRF·상태 코드·배열 개수 상한·원자적 저장·동시 수정 정책은 서버 구현을 위한 설계 제안이다. 이름·전화번호 등 필드 제약과 신입/경력 조건은 기존 가입 계약을 따른다.

Schema 검사는 조회 응답의 필수 필드·회원 역할·Google 이메일 검증 상태·경력 조건·예시를 확인한다. 실제 회원 식별·정지 계정 차단·DB 조회는 `tests/test_worker_profile.py`의 SQLite·MySQL 계약 테스트로 검증한다.

## 기본 정보 부분 수정

`PATCH /api/users/me/profile/basic`

근거 화면: [일반회원 / 기본 정보 수정 (255:1465)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=255-1465).

```json
{ "name": "김지민" }
```

name·phoneNumber·birthDate·gender 중 하나 이상만 제출한다. 생략한 필드는 유지하고 null·빈 객체·알 수 없는 필드는 422다. Google 이메일·ID·역할은 수정할 수 없다. 이름은 앞뒤 공백을 제거한 뒤 검증하고 전화번호는 하이픈 없는 010 번호다. 오늘보다 미래인 생일은 422이며 날짜 기준은 Asia/Seoul이다. 이름·전화번호는 다음 세션 조회에도 반영한다.

수정에는 회원 세션과 X-CSRF-Token 및 허용 Origin이 필요하다. CSRF/Origin 누락·불일치는 403 CSRF_INVALID다. 관리자 password는 받지 않는다. 기본 정보만 원자적으로 저장하고 경력/가능 시간은 유지한다. 200은 전체 프로필이며 실질 변경이 없는 재요청은 updatedAt을 유지한다. 같은 영역을 동시에 저장하면 서버에서 마지막으로 저장된 요청이 반영된다. 다른 영역을 덮어쓰지 않는다.

Schema로 부분 입력·빈 입력/null·읽기 전용 필드 주입·형식/길이 경계를 검증한다. 미래 날짜, 실제 정규화/저장/세션 조회 반영, CSRF·Origin은 `tests/test_profile_basic.py`로 검증한다. 동시 수정은 MySQL 통합 테스트로 검증한다.

## 근무 정보 전체 교체

`PUT /api/users/me/profile/careers`

근거 화면: [근무 정보 수정 (255:1519)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=255-1519), 일반회원 등록의 경력 추가 (255:690), 현재 근무 선택 (506:4178).

```json
{
  "experienceLevel": "EXPERIENCED",
  "careers": [{
    "industry": "CAFE",
    "duties": "음료 제조, 고객 응대",
    "startMonth": "2024-03",
    "endMonth": null,
    "isCurrent": true
  }]
}
```

experienceLevel과 careers를 함께 제출해 근무 정보만 전체 교체한다. 빠진 경력은 삭제하고 요청 순서를 유지한다. NEW는 careers=[]로 기존 경력을 모두 지우며 EXPERIENCED는 1~20건이 필요하다. 최대 20건은 가입 계약에서 이어받은 제안이다.

현재 근무 중은 isCurrent=true/endMonth=null, 종료 경력은 isCurrent=false와 종료 연월이다. 같은 달 시작·종료는 허용하고 미래 연월과 종료<시작은 서버에서 422로 거절한다. 매장명은 선택이며 지우려면 해당 필드를 생략한다. 담당 업무와 매장명은 앞뒤 공백을 제거한 뒤 검증한다. 항목 ID는 받지 않는다.

회원 세션·CSRF/Origin과 저장/재요청/동시성 정책은 기본 정보 수정과 같다. 기본 정보·가능 시간은 유지하고 200으로 전체 프로필을 반환한다. Schema는 신입/경력 전환 조건, 배열 상한, 필수 필드, 현재 근무/종료일 일치, 연월 형식을 검증한다. 날짜 비교·정규화·원자적 교체/삭제·타 영역 보존은 `tests/test_profile_careers.py`의 SQLite·MySQL 통합 테스트로 검증한다.

## 가능 시간 전체 교체

`PUT /api/users/me/profile/availabilities`

근거 화면: [가능 시간 수정 (255:1565)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=255-1565), 일반회원 등록의 가능 시간 추가 (255:692).

```json
{
  "availabilities": [{
    "days": ["SUN"],
    "startTime": "22:00",
    "endTime": "02:00",
    "endsNextDay": true
  }]
}
```

기존 목록 전체를 요청 순서로 교체한다. 기본 정보와 경력은 유지한다. 최소 1건·최대 100건은 기존 가입 계약의 설계 제안으로, 모든 가능 시간을 비우는 요청은 422다. 회원 세션·CSRF/Origin과 저장/재요청/동시성 정책은 기본 정보 수정과 같으며 200은 전체 프로필이다.

Asia/Seoul 기준 매주 반복, 30분 단위이며 0<기간<=24시간이다. 당일 종료는 종료>시작, 다음 날 종료는 종료<=시작이다. 24:00 대신 00:00/endsNextDay를 사용한다. 시작은 포함하고 종료는 제외하므로 인접 구간은 허용한다.

서버는 제출된 전체 목록을 요일별로 펼쳐 같은 날·심야의 다음 날·일요일→월요일 중첩을 검사한다. 예를 들어 일요일 22:00~월요일 02:00과 월요일 01:00~03:00은 겹쳐서 422다. 중복 구간도 422이며 실패 시 기존 목록을 유지한다. 기존 목록과 새 목록 사이의 중첩은 검사하지 않는다.

Schema 검사는 개수 상한·필수 필드·요일 중복·30분 형식·자정 표현·타 영역 주입 거절을 확인한다. 0시간/역전/24시간 초과, 구간 중복·심야/주 경계 중첩, 저장 원자성은 `tests/test_profile_availabilities.py`의 SQLite·MySQL 통합 테스트로 검증한다.

## #106 구현과 검증

- `app/worker_profile.py`가 조회·영역별 수정 API 4개를 제공한다. `app/profile_inputs.py`는 가입 입력의 이름·전화번호·생일·경력·가능 시간 규칙을 재사용한다.
- 기존 `users`·`worker_profiles`·`worker_careers`·`availability_rules`·`availability_days`를 사용하며 스키마·환경변수 변경은 없다. `updatedAt`은 `users.updated_at`으로 반환하고 해당 프로필 영역에 실질 변경이 있을 때만 갱신한다.
- 수정은 인증 조회 트랜잭션을 끝낸 뒤 `User` 행을 `FOR UPDATE`로 잠근다. MySQL `REPEATABLE READ` snapshot이 잠금 이후에 만들어지므로 자식은 일반 조회로도 최신 값을 읽는다. 같은 회원의 영역별 저장을 직렬화해 마지막 저장과 타 영역 보존을 보장한다.
- 자식 행은 `worker_id` 범위 잠금 조회·삭제 없이 기본 키로만 갱신·삭제한다. 기존 행은 위치별로 재사용하고 남는 행만 삭제해 `(worker_id, sort_order)`·`(rule_id, weekday)` 키를 같은 트랜잭션에서 지우고 다시 넣지 않는다. 서로 다른 회원의 동시 수정이 공유 index gap 잠금으로 deadlock(MySQL 1213)되지 않도록 하기 위해서다.
- 그래도 이전 저장이 지운 고유 키(`(rule_id, weekday)`·`(worker_id, sort_order)`)를 purge 전에 다시 넣으면 InnoDB 중복 검사가 공유 gap 잠금을 걸어 드물게 deadlock이 난다(측정 약 2.5%). 스키마 변경 없이는 막을 수 없으므로, 잠금 경합(1213/1205)으로 실패한 저장은 요청 전체를 최대 3회 다시 실행한다. 다른 DB 오류는 재시도하지 않는다.
- 응답 본문은 잠금을 보유한 트랜잭션 안에서 만들고 commit 성공 후 반환한다. 검증·자식 교체·commit 실패는 기존 프로필 전체를 유지한다.
- 경력과 가능 시간 그룹은 요청 순서로 저장한다. 그룹 내 요일은 순서 없는 선택 집합으로 비교하고 응답은 `MON`~`SUN` 순으로 반환한다. 요일 순서만 바꾼 요청은 `updatedAt`을 변경하지 않는다.
- 수정 API 3개는 OpenAPI의 `Idempotency-Key` 필수 대상이 아니다. 회원 세션·CSRF·Origin을 검증하며, 동일한 정규화 내용을 재저장하면 자식 행 ID와 `updatedAt`을 유지한다. 중간에 다른 변경이 있었다면 마지막 요청의 내용을 적용한다.
- `tests/auth_contract.py`는 실제 프로필 성공·오류 응답을 hand-authored OpenAPI schema로 검증한다. `tests/test_profile_transactions.py`는 모든 수정 API의 인증·CSRF·commit 실패·개인정보 로그 차단과 MySQL 동시 수정의 영역 보존을 검증한다.

```bash
cd back-end
python -m ruff check .
python -m pytest tests/test_worker_profile.py tests/test_profile_basic.py \
  tests/test_profile_careers.py tests/test_profile_availabilities.py \
  tests/test_profile_transactions.py
```

실제 행 잠금·동시성은 전용 `*_test` MySQL DB와 `DB_*`를 설정한 뒤 `JIDAN_REQUIRE_MYSQL=1 python -m pytest`로 검증한다. DB 설정 없는 실행에서 MySQL skip은 MySQL 검증 완료를 뜻하지 않는다.
