# 관심 매장 계약

Figma 일반회원 홈에 관심 매장 건수가 있으므로 유지할 데이터 계약을 추가합니다. 목록·등록·해제 상세 화면은 없어 제안 API로 구분합니다. 승인된 매장의 본인 관심 관계만 관리하고 자료 접근·초대·지원과 연결하지 않습니다.

## #117 관심 매장 구현

- `favorite_stores(worker_id, store_id)` 복합 PK, `saved_at`(마이그레이션 0009). 구현은 `app/favorite_stores.py`.
- PUT: ACTIVE WORKER만. APPROVED 매장만 저장하며 대기·없는 매장은 같은 404 `RESOURCE_NOT_FOUND`다. 이미 저장했으면 최초 savedAt으로 200이다. 동시 저장은 PK와 무해한 upsert로 한 행만 남기고, 결과는 잠금 읽기로 조회한다(REPEATABLE READ 스냅샷이 동시 커밋을 놓치지 않도록). 재현 시 매장 승인 상태를 다시 확인한다.
- DELETE: 매장을 조회하지 않고 본인 관계만 지운다. 관계·매장이 없거나 승인이 풀려도 204이며 매장 존재 여부를 드러내지 않는다. 다시 저장하면 새 savedAt이다.
- GET: APPROVED 매장만 `savedAt DESC, storeId DESC`로 반환한다(관계에 별도 ID가 없어 매장 ID로 동시각 순서를 고정). `totalItems`가 홈 관심 매장 건수와 같은 기준이다.
- 응답 `JobStoreCard`는 공개 정보(이름·업종·주소)만 담는다. `neighborhood`는 `app.store_address.SERVICE_NEIGHBORHOOD`(월계1동)다. 저장 주소는 행정동이 없는 Kakao 도로명 주소라 그 문자열에서 행정동을 도출할 수 없다. 대신 가입·매장 추가가 모두 좌표의 행정동이 월계1동인지 검증하므로 저장된 매장은 항상 이 값이다. 서비스 지역이 늘어나면 검증 시 행정동을 컬럼으로 저장해야 한다.
- 명세에 관심 매장 수 상한이 없어 상한을 두지 않는다. 저장은 자료 접근·초대·지원을 만들지 않는다.
