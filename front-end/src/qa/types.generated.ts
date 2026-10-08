// Generated from backend OpenAPI 0.11.0. Regenerate with scripts/generate-qa-contract.mjs.
export type ManualPublicProcessingError = { "code": "AI_PROCESSING_FAILED" | "TRANSCRIPTION_FAILED"; "message": string; "retryable": true }
export type ManualTranscription = { "id": string; "mediaId": string; "status": "RUNNING" | "READY" | "ERROR"; "text": string | null; "error": (ManualPublicProcessingError | null); "createdAt": string; "completedAt": string | null } & ({ "status": "RUNNING"; "text": null; "error": null; "completedAt": null } | { "status": "READY"; "text": string; "error": null; "completedAt": string } | { "status": "ERROR"; "text": null; "error": ManualPublicProcessingError; "completedAt": string })
export type QAAnswer = { "outcome": "ANSWERED" | "NEEDS_OWNER"; "text": string; "citations": Array<QACitation> } & ({ "outcome": "ANSWERED"; "citations": unknown } | { "outcome": "NEEDS_OWNER"; "citations": unknown })
export type QACitation = { "versionId": string; "sectionId": string; "sectionTitle": string; "excerpt": string }
export type QAConversation = { "id": string; "storeId": string; "createdAt": string; "updatedAt": string }
export type QAConversationCreate = {  }
export type QAConversationDetail = { "conversation": QAConversation; "turns": Array<QAQuestion>; "nextBeforeSequence": (number | null) }
export type QAConversationPage = { "items": Array<QAConversation>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type QAMedia = { "id": string; "storeId": string; "purpose": "QUESTION_IMAGE" | "QUESTION_AUDIO"; "mimeType": "image/jpeg" | "image/png" | "image/webp" | "audio/mpeg" | "audio/mp4" | "audio/webm" | "audio/wav"; "sizeBytes": number; "createdAt": string; "expiresAt": string } & ({ "purpose": "QUESTION_IMAGE"; "mimeType": "image/jpeg" | "image/png" | "image/webp"; "sizeBytes": number } | { "purpose": "QUESTION_AUDIO"; "mimeType": "audio/mpeg" | "audio/mp4" | "audio/webm" | "audio/wav" })
export type QAMediaUpload = { "purpose": "QUESTION_IMAGE" | "QUESTION_AUDIO"; "file": string }
export type QAProcessingRetry = {  }
export type QAQuestion = { "id": string; "conversationId": string; "sequence": number; "manualVersionId": string; "status": "RUNNING" | "READY" | "ERROR"; "text": string; "imageMediaIds": Array<string>; "createdAt": string; "completedAt": string | null; "answer": (QAAnswer | null); "error": (ManualPublicProcessingError | null) } & ({ "status": "RUNNING"; "answer": null; "error": null; "completedAt": null } | { "status": "READY"; "answer": QAAnswer; "error": null; "completedAt": string } | { "status": "ERROR"; "answer": null; "error": ManualPublicProcessingError; "completedAt": string })
export type QAQuestionInput = { "kind": "TEXT" | "VOICE"; "text": (string | null); "transcriptionId": string | null; "imageMediaIds": Array<string> } & ({ "kind": "TEXT"; "text": string; "transcriptionId": null } | { "kind": "VOICE"; "text": null; "transcriptionId": string })
export type QATranscriptionCreate = { "mediaId": string }
export type Operations = {
 retryQAQuestionTranscription: {input: QAProcessingRetry; output: ManualTranscription}
 deleteQAUnattachedMedia: {input: undefined; output: void}
 uploadQAQuestionMedia: {input: FormData; output: QAMedia}
 transcribeQAQuestionAudio: {input: QATranscriptionCreate; output: ManualTranscription}
 getQAQuestionMediaContent: {input: undefined; output: Blob}
 getQAQuestionTranscription: {input: undefined; output: ManualTranscription}
 createQAConversation: {input: QAConversationCreate; output: QAConversation}
 listMyQAConversations: {input: undefined; output: QAConversationPage}
 retryManualQuestion: {input: QAProcessingRetry; output: QAQuestion}
 askManualQuestion: {input: QAQuestionInput; output: QAQuestion}
 getManualQuestionResult: {input: undefined; output: QAQuestion}
 getQAConversation: {input: undefined; output: QAConversationDetail}
}
