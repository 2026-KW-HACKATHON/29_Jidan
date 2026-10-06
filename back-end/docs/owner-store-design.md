# 점주 매장 관리 API 설계

[Figma](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1)의 점주 홈과 매장 관리 화면을 위한 계약이다. #107에서 목록·상세·추가·관리 요약 API와 DB 처리를 구현했다.

## 관리 매장 조회

- `GET /api/owners/me/stores`: 세션 점주의 모든 관리 매장을 생성 시각/ID 내림차순으로 페이지 조회한다. 승인 대기 매장도 포함한다.
- `GET /api/stores/{storeId}`: 해당 점주의 매장 기본 정보와 승인 상태·신청 ID·권한을 조회한다. 매장 ID와 승인 신청 ID는 구분한다.

근거: 점주 홈(192:5168), 승인 대기 홈(335:1008). 점주 정보나 매장 소유자를 요청으로 지정하지 않는다. 가입 완료 ACTIVE OWNER의 회원 세션을 요구하고 다른 점주의 매장과 없는 매장은 동일한 404다. 승인 대기에는 READ_STORE_STATUS만, 승인 완료에는 기존 StoreAccess의 운영 권한을 반환한다. 응답은 UI 힌트이고 서버가 매 요청 소유권과 승인 상태를 재확인한다.

페이지 기본 0/20, 최대 100과 응답 asOf 시각을 사용한다. 빈 결과/페이지 이후는 200의 빈 items다. 목록·건수는 한 DB 읽기 스냅샷을 사용하며 페이지 사이의 데이터 변경은 가능하다. 응답은 no-store다. 매장 정보 수정/삭제와 소유권 이전 화면은 확인되지 않아 이번 명세에 추가하지 않는다.

Schema로 상태/승인 시각/권한 일치, 형식·페이지 경계·응답 예시를 검증한다. 실제 점주 인가와 조회 도중 독립 트랜잭션의 쓰기가 발생하는 MySQL 스냅샷을 통합 테스트로 검증한다.

## 매장 추가 신청

`POST /api/stores`

근거: 점주 홈의 ‘매장 추가’(192:5168), 점주 가입 매장 정보(220:401). 가입의 StoreRegistrationInput을 그대로 사용하며 소유자·승인 상태·권한은 입력받지 않는다. 기존 승인 매장이 없어도 가입 완료 OWNER는 신청할 수 있다. 서버가 월계1동 소재지를 주소 데이터로 확인하고 사업자 번호 중복을 검사한다. 중복은 409 STORE_ALREADY_REGISTERED이며 권한 자동 부여는 없다.

회원 세션·CSRF·허용 Origin과 UUID Idempotency-Key가 필요하다. 매장·승인 신청·key 결과를 같은 트랜잭션에 저장해 201의 PENDING 매장을 반환한다. 관리자 조회와 다음 회원 세션/관리 매장 조회에 반영한다. 24시간 내 동일 key/body는 최초 201을 재현하고 다른 body는 409 IDEMPOTENCY_KEY_REUSED다. 실제 MySQL UNIQUE 경합, 동일/다른 점주의 동시 생성, 동일 key 재현과 실패 시 rollback을 통합 테스트로 검증한다.

## 매장 관리 카드 요약

`GET /api/stores/{storeId}/management-summary`

매장 관리 홈(192:5218)의 수락 대기 초대 수·현재 근무자 수·만료 예정 근무자 수를 한 읽기 스냅샷/asOf로 반환한다. 승인된 소유 매장만 가능하다. 초대 건수는 초대 목록 ACTIVE와 같으며, 현재 근무자 수는 유효 자료 접근이 있는 workerId의 서로 다른 개수다. 만료 예정은 모든 유효 접근이 24시간 이내 끝나는 근무자의 개수로 현재 근무자 수의 부분집합이다. 여러 접근이 있는 사람을 중복 계산하지 않는다.

공고 모집/지원 승인과 캘린더 화면은 후속 도메인 범위다. 그 건수는 아직 이번 응답에 넣지 않는다. 시작·종료 시각 경계, 무기한/복수 접근, 정지 계정 제외와 MySQL 조회 중 변경에 대한 스냅샷 일치를 통합 테스트로 검증한다. Schema로 비음수 건수·UUID·시각 형식과 빈 결과를 확인한다.


## #107 구현과 검증

- `app/owner_stores.py`: 회원 세션 OWNER 검사, 매장 소유권 404, 목록/상세 및 멱등 추가 신청. PENDING 매장도 조회와 추가 신청이 가능하다.
- `app/store_summary.py`: 본인 APPROVED 매장만 집계한다. 접근 시작 시각은 포함하고 종료 시각은 제외하며, ACTIVE WORKER의 유효 접근을 workerId별로 묶는다. 무기한 접근이 있거나 하나라도 24시간 이후까지 유효하면 만료 예정에서 제외한다.
- `app/store_address.py`의 기존 Kakao 주소 검증을 재사용한다. `KAKAO_REST_API_KEY`가 필요하다. 새 매장 추가의 fieldErrors는 body와 같은 최상위 필드 경로를 사용한다.
- 기존 매장·승인 신청·자료 접근 테이블을 사용하며 새 마이그레이션은 없다. 권한은 매장 승인 상태에서 계산하므로 승인 후 기존 점주 세션도 다음 조회에서 권한을 받는다.
- API 응답은 실제 OpenAPI Schema와 대조한다. SQLite에서는 정상·인가·입력 경계·실패 rollback을 확인하고, MySQL에서는 동시 생성·UNIQUE 경합·멱등성·읽기 스냅샷을 추가로 확인한다.

```bash
cd back-end
python -m pytest tests/test_owner_stores.py tests/test_store_creation.py tests/test_store_summary.py tests/test_store_concurrency.py tests/test_store_snapshots.py
```

MySQL 검증에는 별도 `_test` DB의 `DB_*`와 `JIDAN_REQUIRE_MYSQL=1`을 설정한다. SQLite 변형에서는 MySQL 전용 동시성·스냅샷 테스트를 건너뛴다. 로컬 검증과 개발 서버 배포는 별개이며, 서버 스키마 적용은 #146의 CI/CD 마이그레이션 절차를 따른다.
