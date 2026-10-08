// Isolated contract examples for UI verification; never imported by production.
import type { ManualInterviewSession,ManualState } from '../manual/types'
export const manualStoreId='51c1c743-d377-4e7a-8449-94377eecfce0'
export const interviewFixture:ManualInterviewSession={
  "id": "c701b623-889c-4d28-b055-501251ac6773",
  "storeId": "51c1c743-d377-4e7a-8449-94377eecfce0",
  "draftVersionId": "a0912ce8-1103-428d-aed0-57a5d78c7f0d",
  "questionSetVersion": 1,
  "revision": 2,
  "status": "IN_PROGRESS",
  "phase": "COLLECTING",
  "intents": [
    {
      "id": "09dbccbd-75e2-4709-9a80-b9a9ddeef10a",
      "key": "WORK_STRUCTURE",
      "stage": "WORK_STRUCTURE",
      "coverage": "PENDING",
      "depth": 0,
      "finishedAt": null
    }
  ],
  "currentIntentId": "09dbccbd-75e2-4709-9a80-b9a9ddeef10a",
  "questions": [
    {
      "id": "08d19610-3c04-4b22-8f56-765af2d236b8",
      "intentId": "09dbccbd-75e2-4709-9a80-b9a9ddeef10a",
      "kind": "BASE",
      "depth": 0,
      "batchId": null,
      "text": "근무는 어떻게\n나누어져 있나요?",
      "guidance": "오전조·오후조처럼 매장에서 부르는 이름으로 알려주세요.",
      "guidanceCards": [{"id":"71000000-0000-4000-8000-000000000001","type":"LIST","title":"근무 시간 예시","items":[{"id":"71000000-0000-4000-8000-000000000002","label":"오전조"},{"id":"71000000-0000-4000-8000-000000000003","label":"오후조"},{"id":"71000000-0000-4000-8000-000000000004","label":"야간조"}]}],
      "answered": false
    }
  ],
  "processing": null,
  "error": null,
  "startedAt": "2026-10-05T01:00:00Z",
  "completedAt": null
}
export const stateFixture:ManualState={
  "storeId": "51c1c743-d377-4e7a-8449-94377eecfce0",
  "currentPublishedVersionId": null,
  "draftVersionId": "a0912ce8-1103-428d-aed0-57a5d78c7f0d",
  "interviewSessionId": "c701b623-889c-4d28-b055-501251ac6773"
}

import type {ManualIntentReview} from '../manual/types'
export const reviewFixture:ManualIntentReview={
  "intentId": "09dbccbd-75e2-4709-9a80-b9a9ddeef10a",
  "revision": 3,
  "status": "READY",
  "content": {
    "intentId": "09dbccbd-75e2-4709-9a80-b9a9ddeef10a",
    "summary": "야간조는 22시부터 다음 날 7시까지입니다.",
    "shifts": [
      {
        "id": "44b35781-3e98-4f28-9505-2178b2863c5e",
        "name": "야간조",
        "startTime": "22:00",
        "endTime": "07:00",
        "endsNextDay": true
      }
    ],
    "sections": [],
    "needsDetail": false,
    "structurePhotos": [
      {
        "mediaId": "5329e5a1-0293-44eb-b11b-69df76660be1",
        "caption": "근무표",
        "title": "업무 참고 사진"
      }
    ]
  },
  "confirmedAt": null,
  "processing": null,
  "error": null
}
