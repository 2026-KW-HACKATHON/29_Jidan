# 업무 매뉴얼·AI 인터뷰·질의응답 ERD

```mermaid
erDiagram
    STORES ||--o| STORE_MANUALS : owns
    STORE_MANUALS ||--o{ MANUAL_VERSIONS : versions
    MANUAL_VERSIONS o|--o| STORE_MANUALS : current_for
    USERS ||--o{ MANUAL_VERSIONS : authors
    MANUAL_VERSIONS ||--o{ MANUAL_SHIFTS : defines
    MANUAL_VERSIONS ||--o{ MANUAL_SECTIONS : contains
    MANUAL_SHIFTS o|--o{ MANUAL_SECTIONS : scopes
    MANUAL_SECTIONS ||--o{ MANUAL_STEPS : describes
    MANUAL_SECTIONS ||--o{ MANUAL_MEDIA : illustrates
    MANUAL_VERSIONS ||--o{ INTERVIEW_SESSIONS : drafts
    USERS ||--o{ INTERVIEW_SESSIONS : interviews
    INTERVIEW_QUESTION_SETS ||--o{ INTERVIEW_INTENTS : defines
    INTERVIEW_QUESTION_SETS ||--o{ INTERVIEW_SESSIONS : seeds
    INTERVIEW_INTENTS ||--o{ INTERVIEW_SESSION_INTENTS : covered_by
    INTERVIEW_SESSIONS ||--o{ INTERVIEW_SESSION_INTENTS : tracks
    INTERVIEW_SESSIONS ||--o{ INTERVIEW_TURNS : records
    INTERVIEW_INTENTS o|--o{ INTERVIEW_TURNS : elicits
    MANUAL_VERSIONS ||--o{ MANUAL_QA : grounds
    USERS ||--o{ MANUAL_QA : asks
    MANUAL_QA ||--o{ MANUAL_QA_CITATIONS : cites
    MANUAL_SECTIONS ||--o{ MANUAL_QA_CITATIONS : sourced_by

    STORES {
        uuid id PK
    }
    USERS {
        uuid id PK
        string role
    }
    STORE_MANUALS {
        uuid id PK
        uuid store_id FK, UK
        uuid current_published_version_id FK
    }
    MANUAL_VERSIONS {
        uuid id PK
        uuid manual_id FK
        int revision_no
        string status
        uuid created_by_owner_id FK
        datetime created_at
        datetime published_at
    }
    MANUAL_SHIFTS {
        uuid id PK
        uuid version_id FK
        int sort_order
        string name
        time start_time
        time end_time
        boolean ends_next_day
    }
    MANUAL_SECTIONS {
        uuid id PK
        uuid version_id FK
        uuid shift_id FK
        int sort_order
        string category
        string title
    }
    MANUAL_STEPS {
        uuid id PK
        uuid section_id FK
        int sort_order
        text instruction
        boolean checklist_item
    }
    MANUAL_MEDIA {
        uuid id PK
        uuid section_id FK
        int sort_order
        string object_key
        string mime_type
        string caption
    }
    INTERVIEW_QUESTION_SETS {
        uuid id PK
        int revision_no UK
        datetime created_at
    }
    INTERVIEW_INTENTS {
        uuid id PK
        uuid question_set_id FK
        int sort_order
        string intent_key
        text base_question
        text coverage_criteria
    }
    INTERVIEW_SESSIONS {
        uuid id PK
        uuid draft_version_id FK
        uuid owner_id FK
        uuid question_set_id FK
        datetime started_at
        datetime completed_at
    }
    INTERVIEW_SESSION_INTENTS {
        uuid session_id PK, FK
        uuid intent_id PK, FK
        string coverage_status
        text coverage_note
        datetime covered_at
    }
    INTERVIEW_TURNS {
        uuid id PK
        uuid session_id FK
        int turn_no
        string speaker
        string turn_kind
        string question_kind
        uuid intent_id FK
        string input_method
        text content
        datetime created_at
    }
    MANUAL_QA {
        uuid id PK
        uuid published_version_id FK
        uuid worker_id FK
        text question
        text answer
        datetime asked_at
        datetime answered_at
    }
    MANUAL_QA_CITATIONS {
        uuid id PK
        uuid qa_id FK
        uuid section_id FK
    }
```

## 테이블과 제약

| 테이블 | 핵심 제약 |
| --- | --- |
| `store_manuals` | `store_id` UNIQUE. `current_published_version_id`는 같은 매뉴얼의 게시 버전만 참조 |
| `manual_versions` | `(manual_id, revision_no)` UNIQUE. `status`는 `DRAFT`/`PUBLISHED`. 초안은 `published_at IS NULL`, 게시본은 게시 시각 필수이며 게시 후 내용 불변 |
| `manual_shifts` | 버전별 오전·오후·야간 등 근무조와 시간. `(version_id, sort_order)` UNIQUE |
| `manual_sections` | `category`는 `COMMON_TASK`/`SHIFT_TASK`/`RULE`/`EQUIPMENT`. `shift_id`가 있으면 같은 버전에 속해야 함. `(version_id, sort_order)` UNIQUE |
| `manual_steps`, `manual_media` | 각 섹션 안에서 순서 UNIQUE. 사진은 파일 본문 대신 저장소 `object_key`와 선택 설명을 보관. 삭제 시 파일 정리 정책 필요 |
| `interview_question_sets`, `interview_intents` | 필수 질문 셋의 버전과 순서를 보관. 각 인텐트는 기본 질문과 확인할 정보의 기준을 가짐. `(question_set_id, sort_order)` 및 `(question_set_id, intent_key)` UNIQUE. 사용한 질문 셋은 수정하지 않고 새 버전을 생성 |
| `interview_sessions`, `interview_session_intents` | AI 인터뷰는 초안 버전에만 연결하고 시작할 때 질문 셋 버전을 고정. 세션에 포함된 모든 필수 인텐트의 수집 상태를 `PENDING`/`NEEDS_DETAIL`/`COVERED`로 추적. `(session_id, intent_id)` UNIQUE이며 인텐트는 세션의 질문 셋에 속해야 함. 각 필수 인텐트에 질문과 점주 답변을 기록하고, `NEEDS_DETAIL`로 남은 항목은 점주 검토 화면에 표시. 미충족 항목의 게시 허용 여부는 별도 정책 결정 |
| `interview_turns` | AI 질문과 점주 답변을 순서대로 저장. `speaker`는 `AI`/`OWNER`, `turn_kind`는 `QUESTION`/`ANSWER`/`OTHER`, AI 질문의 `question_kind`는 `BASE`/`PROBE`. `intent_id`는 세션의 필수 질문 셋에 속하는 인텐트를 참조하며, 실제 질문 문구는 `content`에 저장. 점주 답변 방식은 `VOICE`/`TEXT`, 실제 저장 내용은 텍스트. `(session_id, turn_no)` UNIQUE |
| `manual_qa`, `manual_qa_citations` | 질문에는 당시 게시 버전을 고정하고 출처 섹션은 같은 버전이어야 함. 답변 근거가 없는 경우 사용자에게 근거 부족을 알리는 정책 필요 |

- AI가 필수 질문 셋의 인텐트를 순서대로 확인 → 점주가 답변 → AI가 모호하거나 결과만 말한 답변에서 조건·판단 기준·작업 순서·예외 등 빠진 정보를 추가 질문으로 탐문 → 모든 필수 인텐트를 충족하면 답변을 매뉴얼 초안으로 구조화 → 점주 수정·확인 → 게시 순서다. 기본 질문 문구는 문맥에 맞게 달라질 수 있지만 필수 인텐트 자체는 생략하지 않는다. 추가 질문은 해당 인텐트에 연결한 `PROBE` 턴으로 남긴다. 이 질문 셋은 점주의 암묵지를 수집하는 인텐트 레이어이며, `coverage_criteria`와 실제 답변을 토대로 수집 완료 여부를 판단한다. AI가 만든 초안은 `DRAFT`이고 근무자에게 공개하지 않는다. 게시 시 새 버전을 고정하고 `current_published_version_id`를 원자적으로 바꾼다. 이전 게시 버전과 그 출처는 당시 질의응답의 근거로 유지한다.
- 매뉴얼 조회와 AI 질의응답은 [매장 접근 권한](access.md)을 확인한 근무자에게 현재 게시 버전만 제공한다. 질의응답 저장 행은 당시 버전을 가리켜 이후 수정에도 출처가 달라지지 않는다.
- 사진은 공통 업무·규정 등 연결된 섹션에 붙인다. IR 자료에는 영상 보완 언급도 있지만 현재 상세 화면은 사진 첨부를 구체화했으므로 영상 처리·보존 정책은 후속 결정이다. 음성 원본의 장기 보관도 화면에서 확정되지 않아 이 ERD에는 전사 텍스트만 둔다.
- 화면 근거: [매뉴얼 작성 시작](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=560-4170), [AI 인터뷰](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2713), [최종 검토·게시](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2757), [사진 첨부](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=777-2979), [업무 목록](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2819), [단계별 상세](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=624-2603), [AI 질의응답](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=330-2914), [IR Deck의 AI Rule Book](https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj?node-id=597-2700).
