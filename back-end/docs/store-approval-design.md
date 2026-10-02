# 매장 승인 신청 관리 API 설계

사용자가 요청한 관리자 기능의 구현 전 계약이다. [OpenAPI 원본](../openapi.yaml)과 [Swagger](http://127.0.0.1:5500)에서 확인한다. 실제 관리자 인증과 DB 처리 코드는 아직 없다.

## 전체 신청 조회

`POST /api/admin/store-approval-requests/search`

```json
{
  "password": "<admin-password>",
  "page": 1,
  "size": 20
}
```

- password는 매 요청 body에 필수 입력한다. 위 값은 실제 비밀번호가 아닌 자리 표시자다.
- status 생략 시 모든 점주의 승인 대기·완료 신청을 조회한다. PENDING/APPROVED 필터를 선택할 수 있다.
- 기본 page=1, size=20, 최대 size=100. submittedAt 및 id 내림차순으로 정렬한다.
- 신청 ID, 신청자 식별/연락 정보, 매장 정보, 상태, 신청/승인 시각을 반환한다.
- 빈 결과나 마지막 페이지를 넘긴 요청은 200, 빈 items를 반환한다. 필터와 totalItems는 현재 DB 기준이며 조회 간 데이터 변동이 가능하다.
- 점주 가입/최초 매장 저장과 동일 트랜잭션에서 승인 신청을 생성한다. 최초 PENDING/approvedAt=null 상태다.

## 관리자 인증 계약

기존 Google 회원 세션과 별도로 관리자 공용 password를 body로 받는다. OpenAPI 보안 scheme은 body 인증을 표현할 수 없으므로 operation의 security=[]로 기존 쿠키 조건을 해제하고, requestBody의 password 필수 조건으로 표현한다. 이는 공개 조회를 허용한다는 뜻이 아니다.

- 비밀번호 누락/빈 값/형식 오류: 422. 잘못된 비밀번호: 401 ADMIN_PASSWORD_INVALID.
- 비밀번호 검증 전에는 신청 목록·건수·개인정보를 반환하지 않는다.
- 운영 HTTPS와 서버 설정의 비밀번호 해시 검증을 전제로 한다. 비밀번호를 trim/축약/잘라내기 하지 않는다.
- 프론트 빌드·소스에 하드코딩하거나 브라우저 영구 저장소에 저장하지 않는다.
- 비밀번호는 응답·URL·로그·추적·오류 메시지에서 제외한다. 응답에는 Cache-Control: no-store를 적용한다.
- 관리자 endpoint 및 IP 기준 인증 실패 제한 후 429/Retry-After를 반환한다.
- 공용 password 방식에서는 개인별 관리자를 식별할 수 없다. 운영 기록에는 요청 ID·신청 ID·작업·시각·결과를 남기되 password는 기록하지 않는다.

로그에서 인증 비밀번호를 제외하는 계약과 인증 시도 제한은 [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)을 참고했다.

## 검증 범위

OpenAPI 구조·예시 및 password/페이지/상태 입력 경계, 승인 상태와 시각 일치, 응답의 password 제외를 Schema로 확인한다. 실제 비밀번호 대조·인증 시도 제한·DB 페이지 수 계산·정렬은 서버 구현 후 검증이 필요하다.
