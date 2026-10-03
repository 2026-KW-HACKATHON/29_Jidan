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
