# 점주 매뉴얼 작성 MVP

OpenAPI 0.10.0(PR #136)을 기준으로 합니다. `types.ts`와 `contract.generated.ts`는 선택한 점주 매뉴얼 26개 operation 및 참조 schema의 스냅샷입니다. 전체 내용 편집 등 넓은 API 계약은 유지합니다. `ManualService`는 매장별로 생성하며 `createManualHttpService`에 #123의 CSRF 조회 함수를 주입합니다. 인증 수명주기와 운영 진입은 #131에서 연결합니다.

음성 완료 → 업로드 → 전사 READY 조회 → VOICE 답변 제출을 연속 수행하고 원문 확인은 제공하지 않습니다. 응답 유실 재시도는 같은 키와 본문을 사용합니다. 녹음 Blob은 메모리에만 보관하며 화면 이탈 시 마이크와 타이머를 해제합니다. 20 MiB/120초 상한을 허용하고 미지원·권한 거부·무음·빈 전사는 재녹음을 안내합니다. 백그라운드에서는 상태 조회를 멈춥니다.

`/__manual`과 `/__manual?resume=1`은 `/__preview`의 샘플 전용 진입입니다. 실제 마이크 녹음과 계약에 맞는 모의 서비스로 UI를 검수합니다. 음성 내용에 따른 실제 STT/AI 판단은 #119·#120·#131 구현 후 검증합니다. 샘플 코드·데이터는 운영 빌드에서 제외됩니다.

Figma: [시작](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2670), [질문](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2713), [녹음](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=376-1676), [처리 중](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=376-1711). 공통 AppBar·Button·MobileLayout 및 원본 back.svg(7×14)를 재사용합니다. 안내 카드·예시 등 현재 응답에 없는 presentation 데이터는 질문 문자열에서 추측하거나 임의로 생성하지 않습니다.

질문은 session.phase와 questionId로 렌더링하며 depth나 다음 주제를 프론트에서 계산하지 않습니다. 인텐트 검토의 revision은 세션 revision과 분리합니다. 검토 오류·정정 중에도 질문을 계속 진행하고 이전 요약은 별도로 재진입·확인·건너뛸 수 있습니다. 음성 답변 이력은 완료 여부만 표시하며 전사 원문을 노출하지 않습니다. 모든 검토 READY 여부는 생성 전 확인하고 confirmedAt은 생성 선행 조건으로 사용하지 않습니다. `?case=review`, `review-error`, `depth5`, `ready`로 계약 상태를 검수합니다.

사진은 근무 구조 또는 `sectionId`에 연결합니다. 신규 사진은 `사진 N`, `caption=null`로 저장하고 재조회·재시도·순서 변경에서 저장된 이름을 유지합니다. 이름·설명 입력은 없습니다. JPG·PNG·WebP 10 MiB, 대상별 20장까지 허용합니다. 삭제는 연결 해제이며 공유 미디어 삭제 API를 호출하지 않습니다. 보호된 사진 읽기 결과만 object URL로 표시하고 화면 이탈·대상 교체 시 URL과 요청을 해제합니다.

최종 검토 진입은 최신 세션과 검토 목록의 전체 revision snapshot으로 completion을 호출합니다. 생성 중·실패는 서버 초안/세션으로 재개합니다. READY 초안의 versionId·revision과 정정 target을 고정하고 음성 → 전사 READY → 정정 작업 접수 → 작업 조회 → 저장된 초안 재조회 순으로 반영합니다. 정정 RUNNING 중에는 저장된 내용을 계속 표시하며 추가 정정·게시를 차단합니다. 실패 시 이전 내용은 유지하고 명시적으로 이전 내용을 다시 확인한 후 게시할 수 있습니다. retryable 실패만 작업 재시도를 제공합니다. 무변경 성공은 서버 revision·확인 상태를 유지합니다.

부족 항목 확인은 API에 저장한 결과를 사용하며 미리보기 방문으로 대신하지 않습니다. 게시 버튼은 versionId·최신 조회 revision·부족 항목 ID를 함께 제출합니다. 응답 유실은 같은 요청을 재현하고 성공 응답이 있어야 완료 화면을 표시합니다. revision/version 충돌은 최신 내용을 다시 검토하며 오래된 요청을 자동 재시도하거나 새 revision에 재적용하지 않습니다.

추가 미리보기: `?case=draft`, `missing`, `correction-running`, `correction-error`, `correction-clarify`, `correction-noop`, `generation-error`. 사진·정정 작업은 샘플 서비스의 메모리 상태로만 처리됩니다. 실제 회원 쿠키·CSRF·STT·AI·저장소·운영 진입 및 근무자 게시본/Q&A의 통합 검증은 #123/#118/#119/#120/#121/#131 범위입니다. 이 UI PR에서 관련 이슈를 통합 완료로 종료하지 않습니다.

Figma: [사진 첨부](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=777-2979), [최종 검토](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2757), [근무자 미리보기](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=777-3203). 서버에 있는 section 수와 저장 순서를 사용하며 Figma 샘플의 3페이지를 고정하지 않습니다. MVP에서 제외한 이름·설명·텍스트 내용 편집은 렌더링하지 않습니다.
