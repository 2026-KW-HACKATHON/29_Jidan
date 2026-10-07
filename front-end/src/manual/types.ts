// OpenAPI 0.11.0, PR #159, 52f7524. Runtime constraints remain in contract.generated.ts.
export type ManualContent = { shifts: Array<ManualShift>; sections: Array<ManualSection>; structurePhotos?: Array<ManualPhotoAttachment>; missingInformation?: Array<ManualMissingInformation> }
export type ManualContentUpdate = { expectedVersionId: string; expectedRevision: number; content: ManualContent }
export type ManualDraft = ({ versionId: string; storeId: string; versionNumber: number; revision: number; status: "DRAFT"; generationStatus: "NOT_STARTED" | "RUNNING" | "READY" | "ERROR"; interviewSessionId: string | null; content: (ManualContent | null); issues: Array<ManualReviewIssue>; updatedAt: string; latestCorrection?: (ManualDraftCorrection | null) } & ({ generationStatus: "READY"; content: ManualContent } | { generationStatus: "NOT_STARTED" | "RUNNING" | "ERROR"; content: null; latestCorrection?: null }))
export type ManualDraftCorrection = ({ id: string; versionId: string; baseRevision: number; target: ManualDraftCorrectionTarget; status: "RUNNING" | "SUCCEEDED" | "ERROR"; attempt: number; resultRevision: number | null; error: (ManualDraftCorrectionError | null); createdAt: string; completedAt: string | null } & ({ status: "RUNNING"; resultRevision: null; error: null; completedAt: null } | { status: "SUCCEEDED"; resultRevision: number; error: null; completedAt: string } | { status: "ERROR"; resultRevision: null; error: ManualDraftCorrectionError; completedAt: string }))
export type ManualDraftCorrectionError = ({ code: "AI_PROCESSING_FAILED" | "CORRECTION_CLARIFICATION_REQUIRED" | "MANUAL_REFERENCE_CONFLICT" | "MANUAL_VERSION_CONFLICT" | "REVISION_CONFLICT"; message: string; retryable: boolean } & ({ code: "AI_PROCESSING_FAILED"; retryable: true } | { code: "CORRECTION_CLARIFICATION_REQUIRED" | "MANUAL_REFERENCE_CONFLICT" | "MANUAL_VERSION_CONFLICT" | "REVISION_CONFLICT"; retryable: false }))
export type ManualDraftCorrectionInput = { expectedVersionId: string; expectedRevision: number; target: ManualDraftCorrectionTarget; input: ManualInterviewInput }
export type ManualDraftCorrectionRetry = { expectedVersionId: string; expectedRevision: number }
export type ManualDraftCorrectionTarget = ({ kind: "MANUAL" | "SHIFT" | "SECTION"; targetId: string | null } & ({ kind: "MANUAL"; targetId: null } | { kind: "SHIFT" | "SECTION"; targetId: string }))
export type ManualDraftGenerationInput = { expectedRevision: number; reviewRevisions: Array<ManualReviewRevision> }
export type ManualGuidanceCard = (ManualGuidanceList | ManualGuidanceChecklist | ManualGuidancePhotos)
export type ManualGuidanceChecklist = { id: string; type: "PROGRESS_CHECKLIST"; title: string; items: Array<ManualGuidanceProgressItem>; footer?: string | null }
export type ManualGuidanceItem = { id: string; label: string; description?: string | null }
export type ManualGuidanceList = { id: string; type: "LIST"; title: string; items: Array<ManualGuidanceItem>; footer?: string | null }
export type ManualGuidancePhotoTarget = ({ intentId: string; target: "WORK_STRUCTURE" | "SECTION"; sectionId: string | null } & ({ target: "WORK_STRUCTURE"; sectionId: null } | { target: "SECTION"; sectionId: string }))
export type ManualGuidancePhotos = { id: string; type: "PHOTO_SUGGESTIONS"; title: string; items: Array<ManualGuidanceItem>; footer?: string | null; attachmentTarget: (ManualGuidancePhotoTarget | null) }
export type ManualGuidanceProgressItem = { id: string; label: string; description?: string | null; status: "PENDING" | "CURRENT" | "COMPLETED" | "NEEDS_DETAIL" }
export type ManualIntentReview = ({ intentId: string; revision: number; status: "PROCESSING" | "READY" | "ERROR"; content: (ManualInterviewReview | null); confirmedAt: string | null; processing: (ManualReviewProcessing | null); error: (ManualPublicProcessingError | null) } & ({ status: "READY"; content?: ManualInterviewReview; processing?: null; error?: null } | { status: "PROCESSING"; confirmedAt?: null; processing?: ManualReviewProcessing; error?: null } | { status: "ERROR"; confirmedAt?: null; processing?: ManualReviewProcessing; error?: ManualPublicProcessingError }))
export type ManualIntentReviewList = { sessionId: string; sessionRevision: number; items: Array<ManualIntentReview> }
export type ManualInterviewAnswer = { expectedRevision: number; questionId: string; input: ManualInterviewInput; photoIds?: Array<string> }
export type ManualInterviewConfirm = { expectedRevision: number; confirmed: true }
export type ManualInterviewCorrection = { expectedRevision: number; input: ManualInterviewInput }
export type ManualInterviewInput = ({ method: "TEXT"; text: string } | { method: "VOICE"; transcriptionId: string })
export type ManualInterviewIntent = ({ id: string; key: string; stage: "WORK_STRUCTURE" | "COMMON_TASKS" | "SHIFT_TASKS" | "COMPLEMENTS"; coverage: "PENDING" | "NEEDS_DETAIL" | "COVERED"; depth: number; finishedAt: string | null } & ({ coverage: "PENDING"; finishedAt: null } | { coverage: "COVERED"; finishedAt: string } | { coverage: "NEEDS_DETAIL"; depth: 5; finishedAt: string }))
export type ManualInterviewPhotoUpdate = ({ expectedRevision: number; target: "WORK_STRUCTURE" | "SECTION"; sectionId: string | null; photos: Array<ManualPhotoAttachment> } & ({ target: "WORK_STRUCTURE"; sectionId: null } | { target: "SECTION"; sectionId: string }))
export type ManualInterviewProcessing = { taskId: string; kind: "INITIAL_QUESTION" | "EVALUATION" | "FOLLOWUP_GENERATION" | "DRAFT_GENERATION"; attempt: number }
export type ManualInterviewQuestion = ({ guidance?: string | null; guidanceCards?: Array<ManualGuidanceCard>; id: string; intentId: string; kind: "BASE" | "PROBE"; depth: number; batchId: string | null; text: string; answered: boolean } & ({ kind: "BASE"; depth: 0; batchId: null } | { kind: "PROBE"; depth: number; batchId: string }))
export type ManualInterviewReview = { intentId: string; summary: string; shifts: Array<ManualShift>; sections: Array<ManualSection>; needsDetail: boolean; structurePhotos?: Array<ManualPhotoAttachment>; missingInformation?: Array<ManualMissingInformation> }
export type ManualInterviewSession = ({ lastAnsweredQuestion?: ((ManualInterviewQuestion & { answered?: true }) | null); id: string; storeId: string; draftVersionId: string; questionSetVersion: number; revision: number; status: "IN_PROGRESS" | "ERROR" | "COMPLETED"; phase: "COLLECTING" | "PROCESSING" | "READY_TO_GENERATE" | "GENERATING" | "COMPLETED" | "ERROR"; intents: Array<ManualInterviewIntent>; currentIntentId: string | null; questions: Array<ManualInterviewQuestion>; processing: (ManualInterviewProcessing | null); error: (ManualPublicProcessingError | null); startedAt: string; completedAt: string | null } & ({ status: "IN_PROGRESS"; error: null; completedAt: null; phase: "COLLECTING"; questions: unknown; currentIntentId: string; processing: null } | { status: "IN_PROGRESS"; error: null; completedAt: null; phase: "PROCESSING"; processing: ManualInterviewProcessing; questions: unknown } | { status: "IN_PROGRESS"; error: null; completedAt: null; phase: "GENERATING"; processing: ManualInterviewProcessing; questions: unknown; intents: Array<({ coverage: "COVERED"; finishedAt: string } | { coverage: "NEEDS_DETAIL"; depth: 5; finishedAt: string })> } | { status: "IN_PROGRESS"; error: null; completedAt: null; phase: "READY_TO_GENERATE"; processing: null; questions: unknown; currentIntentId: null; intents: Array<({ coverage: "COVERED"; finishedAt: string } | { coverage: "NEEDS_DETAIL"; depth: 5; finishedAt: string })> } | { status: "ERROR"; phase: "ERROR"; error: ManualPublicProcessingError; processing: ManualInterviewProcessing; completedAt: null } | { status: "COMPLETED"; phase: "COMPLETED"; error: null; processing: null; currentIntentId: null; questions: unknown; completedAt: string; intents: Array<({ coverage: "COVERED"; finishedAt: string } | { coverage: "NEEDS_DETAIL"; depth: 5; finishedAt: string })> }))
export type ManualInterviewStart = Record<string, never>
export type ManualInterviewTurn = ({ id: string; sequence: number; intentId: string; kind: "QUESTION" | "ANSWER" | "CORRECTION"; speaker: "AI" | "OWNER"; questionKind: "BASE" | "PROBE" | null; depth: number; batchId: string | null; replyToQuestionId: string | null; inputMethod: "TEXT" | "VOICE" | null; content: string; photoIds: Array<string>; createdAt: string } & ({ kind: "QUESTION"; speaker: "AI"; questionKind: "BASE" | "PROBE"; replyToQuestionId: null; inputMethod: null; photoIds: unknown } | { kind: "ANSWER"; speaker: "OWNER"; questionKind: null; replyToQuestionId: string; inputMethod: "TEXT" | "VOICE" } | { kind: "CORRECTION"; speaker: "OWNER"; questionKind: null; replyToQuestionId: null; inputMethod: "TEXT" | "VOICE" }))
export type ManualInterviewTurnPage = { items: Array<ManualInterviewTurn>; page: number; size: number; totalItems: number; totalPages: number; asOf: string }
export type ManualIssueAcknowledgement = { expectedVersionId: string; expectedRevision: number; issueIds: Array<string>; confirmed: true; note?: string }
export type ManualMedia = ({ id: string; storeId: string; purpose: "MANUAL_PHOTO" | "INTERVIEW_AUDIO"; mimeType: string; sizeBytes: number; createdAt: string } & ({ purpose: "MANUAL_PHOTO"; mimeType: "image/jpeg" | "image/png" | "image/webp"; sizeBytes: number } | { purpose: "INTERVIEW_AUDIO"; mimeType: "audio/mpeg" | "audio/mp4" | "audio/webm" | "audio/wav"; sizeBytes: number }))
export type ManualMissingInformation = ({ id: string; target: "MANUAL" | "SHIFT" | "SECTION"; targetId: string | null; field: "shifts" | "sections" | "startTime" | "endTime" | "endsNextDay" | "steps"; description: string } & ({ target: "MANUAL"; targetId: null; field: "shifts" | "sections" } | { target: "SHIFT"; targetId: string; field: "startTime" | "endTime" | "endsNextDay" } | { target: "SECTION"; targetId: string; field: "steps" }))
export type ManualPhotoAttachment = { mediaId: string; caption: string | null; title: string }
export type ManualPublicProcessingError = { code: "AI_PROCESSING_FAILED" | "TRANSCRIPTION_FAILED"; message: string; retryable: true }
export type ManualPublishInput = { expectedVersionId: string; expectedRevision: number; confirmed: true; acknowledgedIssueIds: Array<string> }
export type ManualReviewIssue = ({ id: string; intentId: string | null; description: string; status: "OPEN" | "ACKNOWLEDGED"; ownerNote: string | null; acknowledgedAt: string | null } & ({ status: "OPEN"; ownerNote: null; acknowledgedAt: null } | { status: "ACKNOWLEDGED"; acknowledgedAt: string }))
export type ManualReviewProcessing = { taskId: string; kind: "UNDERSTANDING" | "CORRECTION"; attempt: number }
export type ManualReviewRevision = { intentId: string; revision: number }
export type ManualRevisionCommand = { expectedRevision: number }
export type ManualSection = ({ id: string; category: "COMMON_TASK" | "SHIFT_TASK" | "RULE" | "EQUIPMENT"; shiftId: string | null; title: string; steps: Array<ManualStep>; photos: Array<ManualPhotoAttachment> } & ({ category: "SHIFT_TASK"; shiftId: string } | { category: "COMMON_TASK" | "RULE" | "EQUIPMENT"; shiftId: null }))
export type ManualShift = { id: string; name: string; startTime: string | null; endTime: string | null; endsNextDay: boolean | null }
export type ManualState = { storeId: string; currentPublishedVersionId: string | null; draftVersionId: string | null; interviewSessionId: string | null }
export type ManualStep = { id: string; instruction: string; checklistItem: boolean }
export type ManualTranscription = ({ id: string; mediaId: string; status: "RUNNING" | "READY" | "ERROR"; text: string | null; error: (ManualPublicProcessingError | null); createdAt: string; completedAt: string | null } & ({ status: "RUNNING"; text: null; error: null; completedAt: null } | { status: "READY"; text: string; error: null; completedAt: string } | { status: "ERROR"; text: null; error: ManualPublicProcessingError; completedAt: string }))
export type ManualTranscriptionInput = { mediaId: string }
export type ManualUploadInput = { purpose: "MANUAL_PHOTO" | "INTERVIEW_AUDIO"; file: string }
export type ManualWorkerPreview = { preview: true; versionId: string; revision: number; content: ManualContent }
export type PublishedManual = { versionId: string; storeId: string; versionNumber: number; status: "PUBLISHED"; ownerConfirmed: true; content: ManualContent; publishedAt: string }
export type Operations = {
  getOwnerManualState: { input: undefined; output: ManualState }
  getManualDraft: { input: undefined; output: ManualDraft }
  uploadManualMedia: { input: FormData; output: ManualMedia }
  deleteUnusedManualMedia: { input: undefined; output: void }
  readManualPhoto: { input: undefined; output: Blob }
  createManualTranscription: { input: ManualTranscriptionInput; output: ManualTranscription }
  getManualTranscription: { input: undefined; output: ManualTranscription }
  startManualInterview: { input: ManualInterviewStart; output: ManualInterviewSession }
  getManualInterview: { input: undefined; output: ManualInterviewSession }
  getManualInterviewTurns: { input: undefined; output: ManualInterviewTurnPage }
  answerManualInterviewQuestion: { input: ManualInterviewAnswer; output: ManualInterviewSession }
  generateManualDraft: { input: ManualDraftGenerationInput; output: ManualInterviewSession }
  retryManualInterviewProcessing: { input: ManualRevisionCommand; output: ManualInterviewSession }
  replaceManualDraftContent: { input: ManualContentUpdate; output: ManualDraft }
  previewManualForWorker: { input: undefined; output: ManualWorkerPreview }
  acknowledgeManualIssues: { input: ManualIssueAcknowledgement; output: ManualDraft }
  publishManualDraft: { input: ManualPublishInput; output: PublishedManual }
  confirmManualInterviewUnderstanding: { input: ManualInterviewConfirm; output: ManualIntentReview }
  correctManualInterviewUnderstanding: { input: ManualInterviewCorrection; output: ManualIntentReview }
  listManualIntentReviews: { input: undefined; output: ManualIntentReviewList }
  getManualIntentReview: { input: undefined; output: ManualIntentReview }
  retryManualIntentReview: { input: ManualRevisionCommand; output: ManualIntentReview }
  replaceManualInterviewReviewPhotos: { input: ManualInterviewPhotoUpdate; output: ManualIntentReview }
  createManualDraftCorrection: { input: ManualDraftCorrectionInput; output: ManualDraftCorrection }
  getManualDraftCorrection: { input: undefined; output: ManualDraftCorrection }
  retryManualDraftCorrection: { input: ManualDraftCorrectionRetry; output: ManualDraftCorrection }
}
