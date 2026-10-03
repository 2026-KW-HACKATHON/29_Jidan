# 일반회원 프로필 API 설계

[Figma Design](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1)의 일반회원 화면에 따른 구현 전 계약이다. [OpenAPI 원본](../openapi.yaml)과 [로컬 Swagger](http://127.0.0.1:5500)에서 확인한다. 실제 API·DB 처리는 아직 구현하지 않았다.

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

Schema 검사는 조회 응답의 필수 필드·회원 역할·Google 이메일 검증 상태·경력 조건·예시를 확인한다. 실제 회원 식별·정지 계정 차단·DB 조회는 서버 구현 후 통합 테스트가 필요하다.

## 기본 정보 부분 수정

`PATCH /api/users/me/profile/basic`

근거 화면: [일반회원 / 기본 정보 수정 (255:1465)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=255-1465).

```json
{ "name": "김지민" }
```

name·phoneNumber·birthDate·gender 중 하나 이상만 제출한다. 생략한 필드는 유지하고 null·빈 객체·알 수 없는 필드는 422다. Google 이메일·ID·역할은 수정할 수 없다. 이름은 공백 정규화 후 검증하고 전화번호는 하이픈 없는 010 번호다. 오늘보다 미래인 생일은 422이며 날짜 기준은 Asia/Seoul이다. 이름·전화번호는 다음 세션 조회에도 반영한다.

수정에는 회원 세션과 X-CSRF-Token 및 허용 Origin이 필요하다. CSRF/Origin 누락·불일치는 403 CSRF_INVALID다. 관리자 password는 받지 않는다. 기본 정보만 원자적으로 저장하고 경력/가능 시간은 유지한다. 200은 전체 프로필이며 실질 변경이 없는 재요청은 updatedAt을 유지한다. 같은 영역을 동시에 저장하면 서버에서 마지막으로 저장된 요청이 반영된다. 다른 영역을 덮어쓰지 않는다.

Schema로 부분 입력·빈 입력/null·읽기 전용 필드 주입·형식/길이 경계를 검증한다. 미래 날짜, 실제 정규화/저장/세션 조회 반영, CSRF·Origin·동시성은 실제 서버의 통합 테스트 대상이다.

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

회원 세션·CSRF/Origin과 저장/재요청/동시성 정책은 기본 정보 수정과 같다. 기본 정보·가능 시간은 유지하고 200으로 전체 프로필을 반환한다. Schema는 신입/경력 전환 조건, 배열 상한, 필수 필드, 현재 근무/종료일 일치, 연월 형식을 검증한다. 날짜 비교·정규화·원자적 교체/삭제·타 영역 보존은 후속 서버 통합 테스트 대상이다.
