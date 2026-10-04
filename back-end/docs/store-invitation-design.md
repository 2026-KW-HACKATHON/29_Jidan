# 근무자 초대 API 설계

Figma 초대 생성(220:1333), 수락(248:1424), 관리(506:4035), 지난 초대(506:4082), 취소(506:4114), 재전송(506:4669)을 근거로 한 구현 전 계약이다. 실제 API·DB·메일 발송은 구현하지 않았다.

## 초대 생성과 접근 기간

`POST /api/stores/{storeId}/invitations`

```json
{
  "email": "jisu@example.com",
  "accessExpiresAt": "2026-11-01T00:00:00+09:00"
}
```

가입 완료 ACTIVE OWNER의 회원 세션·CSRF·허용 Origin이 필요하며 현재 매장 소유권과 APPROVED를 검증한다. 없는/타 점주 매장은 404, 본인의 승인 대기는 403 STORE_APPROVAL_REQUIRED다. body password는 관리자 승인 전용이므로 사용하지 않는다.

사용자가 정한 정책에 따라 초대할 때 접근 종료일을 지정할 수 있고, 종료일과 무관하게 점주가 직접 접근을 종료할 수 있다. accessExpiresAt 생략/null은 별도 종료 전까지다. 종료 시각은 배타적이며 생성 시각보다 미래여야 한다. ‘10월 31일까지’는 KST 11월 1일 00:00으로 보낸다. 서버는 UTC로 정규화해 반환한다. 초대 생성 화면에 해당 입력은 아직 없어 프론트엔드에 추가할 항목이다. 수락 화면에는 이 접근 기간과 링크 유효 기간을 구분해 표시한다.

초대 링크는 생성 후 7일이며 accessExpiresAt과 별개다. 두 종료 시각 중 먼저 도달하면 대기 초대는 EXPIRED다. 수락/거절/취소가 완료된 초대는 그 상태를 유지한다. 같은 매장/정규화 이메일의 유효 PENDING 초대 및 이미 유효한 근무 접근은 각각 409로 중복 생성하지 않는다. 종료된 접근만 있으면 재초대 가능하다. 자기 이메일은 422다.

이메일은 앞뒤 공백 제거 후 전체 소문자로 저장·비교하며 점/plus 별칭은 통합하지 않는다. 초대 이메일로 기존 계정을 병합하지 않는다. 아직 가입하지 않은 이메일도 초대 가능하지만 수락은 검증된 Google 이메일이 일치하는 가입 완료 WORKER만 허용한다.

UUID Idempotency-Key를 요구하며 주체·경로·정규화 body 기준 24시간 재시도 시 최초 결과를 재현한다. 다른 body에는 409다. 초대와 메일 outbox를 원자적으로 저장하고 201의 deliveryStatus=QUEUED를 반환한다. 실제 발송/수신 완료를 의미하지 않는다. lastSentAt은 마지막 발송 요청의 큐 기록 시각이다. 큐 기록 실패는 503 DELIVERY_UNAVAILABLE과 전체 rollback이다.

서버의 256bit 이상 불투명 링크 토큰은 hash만 저장한다. 메일 링크의 토큰은 고정된 frontend origin의 fragment에 두며 프론트는 history에서 제거하고 body로 제출한다. 원문 토큰/초대 링크는 응답·query·로그·분석·오류에 포함하지 않는다. 전달 재시도를 위한 메일 outbox는 수신자/본문의 민감 데이터를 제한된 저장소에 암호화해 보관하고 발송 완료/실패 보존 기한 뒤 폐기한다. 토큰 hash 저장만으로 메일 본문 저장까지 보호됐다고 간주하지 않는다.

초대 상태/시각·수락자 일치, 이메일/기간 입력 형식과 추가 필드 거절을 Schema로 검증한다. 미래 시각·7일 계산·메일 큐·정규화·중복/소유권 검증은 실제 서버 통합 테스트 대상이다.

## 보낸 초대 조회

`GET /api/stores/{storeId}/invitations?view=ACTIVE&page=1&size=20`

ACTIVE는 수락 가능한 PENDING, PAST는 ACCEPTED/DECLINED/CANCELED/EXPIRED다. 완료된 상태는 시간이 지나도 유지한다. 대기 상태에서 링크 기한 또는 지정 접근 기한이 지났으면 DB 배치 상태 변경과 관계없이 EXPIRED로 계산한다. createdAt/ID 내림차순 정렬이며 activeCount/pastCount는 필터와 페이지에 무관한 해당 매장 전체 탭 건수다. items·totalItems·전체 탭 건수와 asOf는 같은 DB 스냅샷 기준이다. 빈 결과/페이지 이후는 200이다. 점주의 승인된 매장만 조회 가능하며 응답에 토큰/링크를 포함하지 않는다. 상태 계산·집계는 후속 실제 서버 통합 검증 대상이다.

## 재전송

`POST /api/stores/{storeId}/invitations/{invitationId}/resend`

body 없이 회원 세션·CSRF·Origin과 UUID Idempotency-Key로 요청한다. 새 요청은 유효 PENDING만 가능하며 수락/거절/취소는 409, 대기 기한 종료는 410이다. 메일 큐·lastSentAt·token hash를 한 트랜잭션으로 변경하고 이전 링크는 무효화한다. 생성 시각·7일 expiresAt·accessExpiresAt·수신 주소는 유지한다. 재전송 실패는 503과 rollback이며 기존 링크는 유지한다. 200의 QUEUED는 발송 요청 기록이다.

같은 key의 재요청은 현재 인증/소유권/승인 확인 후 최초 성공 기록을 먼저 조회해 추가 메일 없이 재현한다. 이후 초대 상태가 바뀌었어도 최초 결과가 재현될 수 있으므로 최신 상태는 목록 조회로 확인한다. 이메일 발송 요청 기록·토큰 교체·재시도/수락과의 경쟁은 실제 서버 통합 테스트 대상이다.

## 수락 대기 초대 취소

`POST /api/stores/{storeId}/invitations/{invitationId}/cancel`

body 없이 회원 세션·CSRF·Origin으로 요청한다. 유효 PENDING을 CANCELED로 바꾸고 200을 반환한다. 이미 CANCELED면 기한이 지나도 최초 canceledAt을 유지해 200이다. ACCEPTED/DECLINED는 409, 기한이 지난 PENDING/EXPIRED는 410이다. 수락한 사람의 접근은 초대 취소로 종료하지 않고 근무자 관리에서 종료한다. 수락/취소는 잠금/조건부 갱신으로 경쟁을 처리한다. 취소 후 미발송 outbox는 폐기하고 이미 발송한 링크는 상태 검증으로 거절한다. 실제 링크 차단·시각 유지·메일 처리·경쟁은 서버 통합 검증 대상이다.
