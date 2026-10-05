import type {ManualDraft} from '../manual/types'
export const draftFixture:Extract<ManualDraft,{generationStatus:'READY'}>={
  "versionId": "a0912ce8-1103-428d-aed0-57a5d78c7f0d",
  "storeId": "51c1c743-d377-4e7a-8449-94377eecfce0",
  "versionNumber": 1,
  "revision": 1,
  "status": "DRAFT",
  "generationStatus": "READY",
  "interviewSessionId": "c701b623-889c-4d28-b055-501251ac6773",
  "content": {
    "shifts": [
      {
        "id": "44b35781-3e98-4f28-9505-2178b2863c5e",
        "name": "야간조",
        "startTime": "22:00",
        "endTime": "07:00",
        "endsNextDay": true
      }
    ],
    "sections": [
      {
        "id": "00c38e75-0e6a-43c1-8fc8-644e969842e5",
        "category": "COMMON_TASK",
        "shiftId": null,
        "title": "재고 정리",
        "steps": [
          {
            "id": "15a9721b-2e65-4d03-8fd0-a5999e19d27a",
            "instruction": "먼저 들어온 제품을 앞쪽에 진열하세요.",
            "checklistItem": true
          }
        ],
        "photos": []
      }
    ]
  },
  "issues": [],
  "updatedAt": "2026-10-05T01:00:00Z"
}
