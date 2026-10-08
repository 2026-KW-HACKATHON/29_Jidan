# Figma 잔여 API 명세 목록

기준: [KW-HACKATHON Figma](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1), 이슈 #100. 기존 53개 operation과 화면을 대조하여 현재 MVP에 필요한 46개를 추가한 목록입니다. 현재 OpenAPI 0.10.0은 초안 정정 3개를 포함해 총 102개 operation입니다. 이 문서는 구현 완료를 의미하지 않습니다.

## 이미 명세가 있는 챕터

Google 인증·가입·세션, 관리자 매장 승인, 일반회원 프로필, 점주 매장 추가·관리, 이메일 초대·수락·거절, 근무자 접근 관리, 점주 AI 인터뷰·사진·전사·검토·게시, 근무자 게시 매뉴얼 목록·상세.

## 추가한 챕터와 화면

| 챕터 | 실제 Figma 화면 | 추가 계약 |
| --- | --- | --- |
| 점주 공고 등록·관리 | 638:2597, 639:2712, 639:2775, 698:3186, 698:3259, 698:3396, 516:4923, 521:2384, 639:2895 | 공고 생성, 매장 공고 목록·상세, 모집 마감 |
| 공고 탐색·상세 | 468:2984, 468:3196, 468:2992 | 검색·업종·근무일·시간대·최신순 목록, 공고 상세 |
| 지원·철회·지원서 | 530:3183, 530:3201, 635:2273, 635:2483, 506:3929, 506:3969, 506:3996, 639:2971, 693:2438, 698:3684, 468:3000 | 자기소개 제출, 내 신청 목록·상세·철회, 점주 지원자 목록·지원서/경력 열람 |
| 근무 요청·확정·온보딩 | 1169:3043, 1169:3054, 698:2714, 698:2765, 698:2821, 698:2918, 698:2981 | 요청 발송·현황·철회, 본인 요청 조회·수락/거절, 미응답, 확정·확정 철회·접근 연결, 점주 온보딩 조회 |
| 공통 알림 | 506:3834, 506:4373, 506:4388, 506:3842, 506:4503, 506:4518 | 전체/안 읽음 목록, 안 읽음 건수, 개별 읽음 처리 |
| 알림에서 초대 수락 연결 | 506:3834, 248:1424 | 본인 검증 이메일 기준 받은 초대 목록·상세·응답 (기존 이메일 token API와 같은 상태 전이) |
| 근무자 매장 선택 | 624:2990, 560:4206, 624:2626 | 접근 가능한 매장 목록, 매장별 현재 접근 상태 |
| 홈 캘린더 | 192:5168, 223:5461 | 점주/근무자 전체 매장 대타 월별 조회만 제공; 정기/일반 일정·편집·단일 상세 MVP 제외 |
| 홈·관심 매장 | 192:5168, 223:5461 | 점주/근무자 홈 요약, 추천 공고, 활동 건수, 관심 매장 목록·등록·해제 |
| 근무자 AI 업무 질문 | 330:2914, 624:2626 | 대화 생성·목록·복원, 질문·답변 상태·재시도, 게시 매뉴얼 근거, 사진/음성 입력·전사 |

각 챕터는 명세·예시·관련 계약 테스트를 함께 검증하고 독립 커밋했습니다. 등록 3단계의 임시 입력과 필터 모달은 프론트엔드 상태이며 단계마다 서버 API를 만들지 않습니다.

## 화면 근거와 설계 제안의 구분

- Figma의 공고 모집 인원은 1명이며 등록 화면에 인원 입력이 없습니다. 이번 계약은 단일 근무자 모집으로 제한합니다.
- 사용자 확정: 요청 기한은 min(요청+1시간, 근무 시작)입니다. 08:30 요청·09:00 근무이면 09:00 만료입니다.
- 근무 요청 수락·거절 상세 화면은 없지만 요청→확정 흐름을 실행하는 데 필요하므로 보완 API로 추가합니다.
- 사용자 범위 조정: 캘린더는 전체 매장 월별 조회만 남기고 확정 대타/완료 이력만 반환합니다. 정기 근무·매장 일반 일정·편집·단일 일정 상세 API는 MVP 제외입니다. 초대 접근 기간을 일정으로 간주하지 않습니다.
- 사용자 첨부 수정 디자인: 수락 대기는 요청 철회, 근무 확정은 확정 철회 버튼입니다. 수락 시 자동 마감하고 근무 시작 전 확정 철회 시 다시 모집 중으로 엽니다. 재개 후 수동 마감은 별도 동작입니다. 해당 대타 접근과 캘린더 연결을 해제하며 다른 정기/대타 접근 및 이력은 유지합니다.
- 사용자 확정: 신청 철회 후 공고 페이지에서 새 지원서로 재지원할 수 있습니다. 추천은 시간 일치 우선, 그 안에서 날짜 순입니다. 전체 근무를 가능 시간 합집합이 덮으면 일치로 정의하고 일부 일치/미등록은 후순위로 표시합니다.
- 관심 매장 건수는 홈에 있으나 추가/해제 화면은 없습니다. 데이터 유지에 필요한 최소 API를 제안으로 구분합니다.
- 최신 매뉴얼 상세에는 체크박스나 완료 저장 버튼이 없습니다. 체크리스트 수행·완료 API는 추가하지 않습니다. 기존 READ_CHECKLISTS는 향후 예약 권한입니다.
- 업무 질문 사진/음성 입력은 버튼이 있으므로 근무자가 이용할 별도 미디어·전사 계약이 필요합니다. 점주 전용 인터뷰 미디어 API에 근무자 권한을 섞지 않습니다.
- 소개 페이지, iPhone 목업 root, 경력 연월/시간 선택 모달, 성공/경고/오류 안내 모달은 정적 UI입니다. 별도 API가 필요하지 않습니다.
- 근태 출퇴근, 실제 보수 지급, 공고 수정/삭제, 매장 승인 거절, 푸시 기기 등록, 계정 탈퇴 화면은 확인되지 않았으므로 별도 기능으로 취급합니다.

## 검증의 경계

OpenAPI lint와 JSON Schema/예시/상태 계약 테스트는 구현 계약을 검증합니다. OAuth, DB 트랜잭션, 알림 발송, AI 추론 및 파일 저장의 실제 동작은 향후 구현 테스트가 필요합니다. 로컬 5500 서버는 Swagger 문서 서버입니다.

## 추가한 endpoint 전체 목록

현재 YAML에서 기존 53개와 비교한 순증 46개입니다. 기존 0.6.0의 49개에서 요청 철회·확정 철회·점주 전체 캘린더 조회 3개를 추가하고, 매장 캘린더 조회·편집 및 단일 상세 6개를 제외했습니다. 각 operation의 요청/응답·예시·권한·오류를 Swagger에서 검수할 수 있습니다. 수정된 버튼은 사용자 첨부 디자인을 우선 기준으로 삼았습니다.

### [점주 대타 공고](job-posting-design.md) — 4개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/stores/{storeId}/job-postings/{jobId}` | [관리 공고 상세](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=639-2971) |
| POST | `/api/stores/{storeId}/job-postings/{jobId}/closure` | [지원자 선정 없이 모집 마감 또는 마감 상태 재확인](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=698-2765) |
| POST | `/api/stores/{storeId}/job-postings` | [대타 공고 등록 (3단계 최종 제출)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=639-2775) |
| GET | `/api/stores/{storeId}/job-postings` | [매장 공고 목록 (모집 중/마감)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=516-4923) |

### [대타 공고 탐색](job-search-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/job-postings` | [대타 공고 검색·필터·최신순](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=468-2984) |
| GET | `/api/job-postings/{jobId}` | [대타 공고 상세·지원 가능 여부](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=468-2992) |

### [일반회원 공고 지원](application-design.md) — 4개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/users/me/applications/{applicationId}` | [내 지원 상세·승인 대기·철회 확인](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=635-2273) |
| POST | `/api/users/me/applications/{applicationId}/withdrawal` | [공고 신청 철회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=635-2483) |
| POST | `/api/job-postings/{jobId}/applications` | [자기소개 작성 후 공고 지원](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=530-3183) |
| GET | `/api/users/me/applications` | [신청 중·확정·종료 공고 목록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=506-3929) |

### [점주 지원자 확인](applicant-review-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/stores/{storeId}/job-postings/{jobId}/applications` | [점주 공고 지원자 목록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=639-2971) |
| GET | `/api/stores/{storeId}/job-postings/{jobId}/applications/{applicationId}` | [지원서·근무자 프로필 열람](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=468-3000) |

### [대타 근무 요청](work-request-design.md) — 8개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/users/me/work-requests/{requestId}` | [알림에서 본인 근무 요청 조회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1) |
| GET | `/api/users/me/work-requests` | [본인 대타 근무 요청 목록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1) |
| GET | `/api/stores/{storeId}/job-postings/{jobId}/work-requests` | [수락 대기·미응답·확정 근무 요청 현황](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=698-2765) |
| GET | `/api/stores/{storeId}/job-postings/{jobId}/onboarding` | [확정된 근무자 온보딩 연결 조회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=698-2821) |
| POST | `/api/users/me/work-requests/{requestId}/response` | [근무 요청 수락·거절과 근무 확정](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=0-1) |
| POST | `/api/stores/{storeId}/job-postings/{jobId}/applications/{applicationId}/work-requests` | [선택 지원자에게 근무 요청](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=1169-3043) |
| POST | `/api/stores/{storeId}/job-postings/{jobId}/work-requests/{requestId}/withdrawal` | [점주 근무 요청 철회 (수락 대기)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=698-2714) |
| POST | `/api/stores/{storeId}/job-postings/{jobId}/work-requests/{requestId}/confirmation-withdrawal` | [점주 근무 확정 철회 (근무 시작 전)](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=698-2821) |

### [일반회원 초대함](invitation-inbox-design.md) — 3개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/users/me/store-invitations/{invitationId}` | [본인 매장 초대 상세와 현재 상태](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=248-1424) |
| GET | `/api/users/me/store-invitations` | [알림에서 받은 매장 초대 조회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=506-3834) |
| POST | `/api/users/me/store-invitations/{invitationId}/response` | [초대함에서 매장 초대 수락·거절](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=248-1424) |

### [공통 알림](notification-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| POST | `/api/users/me/notifications/{notificationId}/read` | [선택 알림 읽음 처리](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=506-4373) |
| GET | `/api/users/me/notifications` | [전체·안 읽은 알림과 안 읽음 건수](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=506-3834) |

### [근무자 매장 선택](worker-store-selection-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/users/me/stores` | [매뉴얼·AI 질문에서 선택할 접근 가능 매장](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=624-2990) |
| GET | `/api/users/me/stores/{storeId}/access` | [선택 매장의 본인 자료 접근 재확인](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=624-2626) |

### [근무 캘린더](calendar-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/users/me/calendar/events` | [본인의 전체 매장 대타 월별 캘린더](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=223-5461) |
| GET | `/api/owners/me/calendar/events` | [점주의 전체 소유 매장 대타 월별 캘린더](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=192-5168) |

### [관심 매장](favorite-store-design.md) — 3개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| PUT | `/api/users/me/favorite-stores/{storeId}` | [관심 매장 등록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=223-5461) |
| DELETE | `/api/users/me/favorite-stores/{storeId}` | [관심 매장 해제](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=223-5461) |
| GET | `/api/users/me/favorite-stores` | [홈 관심 매장 목록·건수](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=223-5461) |

### [홈 요약](home-design.md) — 2개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| GET | `/api/owners/me/home` | [점주 홈 관리 매장·모집 공고·알림 요약](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=192-5168) |
| GET | `/api/users/me/home` | [일반회원 홈 활동·추천 공고 요약](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=223-5461) |

### [AI 질문 미디어](qa-media-design.md) — 6개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| POST | `/api/stores/{storeId}/manual/qa/transcriptions/{transcriptionId}/retries` | [질문 음성 전사 실패 재시도](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| DELETE | `/api/stores/{storeId}/manual/qa/media/{mediaId}` | [전송 전 본인 질문 미디어 삭제](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| POST | `/api/stores/{storeId}/manual/qa/media` | [AI 질문 사진·음성 업로드](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| POST | `/api/stores/{storeId}/manual/qa/transcriptions` | [근무자 질문 음성 비동기 전사](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| GET | `/api/stores/{storeId}/manual/qa/media/{mediaId}/content` | [본인 AI 질문 미디어 보호 조회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| GET | `/api/stores/{storeId}/manual/qa/transcriptions/{transcriptionId}` | [본인 질문 전사 상태·텍스트 조회](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |

### [근무자 AI 업무 질문](qa-conversation-design.md) — 6개

| Method | Path | 화면 동작 |
| --- | --- | --- |
| POST | `/api/stores/{storeId}/manual/qa/conversations` | [매장 AI 업무 질문 대화 시작](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| GET | `/api/stores/{storeId}/manual/qa/conversations` | [현재 매장의 본인 질문 대화 복원 목록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| POST | `/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{questionId}/retries` | [AI 업무 답변 실패 재시도](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| POST | `/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions` | [게시 매뉴얼에 근거한 AI 업무 질문](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| GET | `/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{questionId}` | [AI 질문 처리 상태·답변·매뉴얼 근거](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |
| GET | `/api/stores/{storeId}/manual/qa/conversations/{conversationId}` | [본인 대화·질문·답변 복원](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914) |

## 기존 계약 보완

게시 매뉴얼 목록의 선택 expectedVersionId와 섹션 상세의 409 MANUAL_VERSION_CHANGED로 과거 AI 답변을 새 게시본에 잘못 연결하는 것을 막습니다. 질문 하나 → 답변 하나 → Jev 판단, depth 5 검수중 이후 다음 인텐트 진행, 점주 확인 발행 정책은 유지합니다.

새로 확정한 요청 기한·확정 철회·재지원·캘린더 범위·추천 순서는 설계 제안으로 남겨두지 않습니다. 화면에 없는 관심 매장 등록/해제, 첫 매장 선택과 집계 상한, 파일 보관 상한 및 근무 중첩 차단은 기존 보완 설계로 유지합니다.

0.8.0은 수락 시 CLOSED/closedAt 기록, 확정 철회 시 RECRUITING/closedAt=null 복구, 마감 재요청의 이력 보존으로 상태 전이를 보정합니다. 0.7.0의 캘린더 편집/개별 상세 API 제거, TEMPORARY_WORK만 허용하는 이벤트, WORK_SCHEDULE target의 storeId/workDate 필수화, 추천 카드 matchesAvailability 필수화와 철회 상태 추가를 포함하는 계약 변경입니다. 업무 API 구현 또는 데이터 마이그레이션을 수행한 것은 아닙니다. 설계 Swagger 제공 endpoint는 별도 문서 경로이며 [개발 CI/CD와 주소](README.md#개발-서버-cicd와-endpoint)에 정리했습니다.

0.9.0은 초안 내용 수정·부족 항목 확인·게시 요청에 expectedVersionId를 필수 추가한다. 초안 교체 충돌과 멱등성 처리 규칙은 [매뉴얼 계약](manual-interview-design.md#초안-교체와-오래된-탭-보호-openapi-090)을 따른다.


## 초안 음성 정정 추가 (0.10.0)

#99 후속 계약으로 `POST /api/stores/{storeId}/manual/draft/corrections`, `GET /api/stores/{storeId}/manual/draft/corrections/{correctionId}`, `POST /api/stores/{storeId}/manual/draft/corrections/{correctionId}/retries`를 추가한다. 최종 검토에서 생성된 초안을 음성으로 정정하며 인터뷰 완료 상태를 되돌리지 않는다. 상태·동시성·호환성은 [매뉴얼 계약](manual-interview-design.md#생성-완료된-초안의-음성-정정-openapi-0100)을 따른다.
