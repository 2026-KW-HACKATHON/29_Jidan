// Generated from deployed OpenAPI 0.10.1. Regenerate with scripts/generate-api-contract.mjs.
export type ApplicantSnapshot = { "workerId": string; "name": Name; "ageAtSubmission": number; "experienceLevel": "NEW" | "EXPERIENCED"; "careers": Array<Career> } & ({ "experienceLevel": "NEW"; "careers": unknown } | { "experienceLevel": "EXPERIENCED"; "careers": unknown })
export type ApplicationCreate = { "introduction": string }
export type ApplicationRevision = { "expectedRevision": number }
export type Availability = { "days": Array<"MON" | "TUE" | "WED" | "THU" | "FRI" | "SAT" | "SUN">; "startTime": string; "endTime": string; "endsNextDay": boolean }
export type CalendarEvent = { "id": string; "store": JobStoreCard; "kind": "TEMPORARY_WORK"; "title": string; "startAt": string; "endAt": string; "allDay": false; "workerId": string; "workerName": Name; "jobId": string; "revision": number; "editable": false }
export type CalendarMonth = { "month": string; "timezone": "Asia/Seoul"; "events": Array<CalendarEvent>; "asOf": string }
export type Career = { "industry": Industry; "duties": string; "storeName"?: string; "startMonth": string; "endMonth": string | null; "isCurrent": boolean } & ({ "isCurrent": true; "endMonth": null } | { "isCurrent": false; "endMonth": string })
export type FavoriteStore = { "store": JobStoreCard; "savedAt": string }
export type FavoriteStorePage = { "items": Array<FavoriteStore>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type FavoriteStoreSave = {  }
export type GoogleIdentity = { "provider": "GOOGLE"; "email": string; "emailVerified": true }
export type Industry = "RESTAURANT" | "CAFE" | "CONVENIENCE_STORE" | "OTHER"
export type JobApplication = { "id": string; "job": JobPosting; "status": "APPLIED" | "REQUESTED" | "CONFIRMED" | "WITHDRAWN" | "NOT_SELECTED" | "COMPLETED"; "introduction": string; "submittedAt": string; "withdrawnAt": string | null; "revision": number } & ({ "status": "WITHDRAWN"; "withdrawnAt": string } | { "status": "APPLIED" | "REQUESTED" | "CONFIRMED" | "NOT_SELECTED" | "COMPLETED"; "withdrawnAt": null })
export type JobApplicationPage = { "items": Array<JobApplication>; "page": number; "size": number; "totalItems": number; "asOf": string; "counts": { "pending": number; "confirmed": number; "ended": number } }
export type JobClosure = { "expectedRevision": number }
export type JobOnboarding = { "jobId": string; "workerId": string; "workerName": Name; "accessStatus": "ACTIVE" | "ENDED"; "manualStatus": "PUBLISHED" | "NOT_PUBLISHED"; "manualVersionId": string | null } & ({ "manualStatus": "PUBLISHED"; "manualVersionId": string } | { "manualStatus": "NOT_PUBLISHED"; "manualVersionId": null })
export type JobPosting = { "title": string; "description": string; "workPart": "WEEKDAY_OPEN" | "WEEKDAY_CLOSE" | "WEEKEND_OPEN" | "WEEKEND_CLOSE" | "OTHER"; "workDate": string; "startTime": string; "endTime": string; "endsNextDay": boolean; "minimumExperience": "ANY" | "MONTHS_3" | "MONTHS_6" | "YEAR_1"; "experienceNotes": string; "hourlyPay": number; "paymentTiming": "WORK_DAY" | "NEXT_DAY" | "NEGOTIABLE"; "paymentNotes": string; "id": string; "store": JobStoreCard; "status": "RECRUITING" | "CLOSED"; "recruitmentCount": 1; "applicantCount": number; "startAt": string; "endAt": string; "estimatedPay": number; "createdAt": string; "closedAt": string | null; "revision": number } & ({ "status": "RECRUITING"; "closedAt": null } | { "status": "CLOSED"; "closedAt": string })
export type JobPostingCreate = { "title": string; "description": string; "workPart": "WEEKDAY_OPEN" | "WEEKDAY_CLOSE" | "WEEKEND_OPEN" | "WEEKEND_CLOSE" | "OTHER"; "workDate": string; "startTime": string; "endTime": string; "endsNextDay": boolean; "minimumExperience": "ANY" | "MONTHS_3" | "MONTHS_6" | "YEAR_1"; "experienceNotes": string; "hourlyPay": number; "paymentTiming": "WORK_DAY" | "NEXT_DAY" | "NEGOTIABLE"; "paymentNotes": string }
export type JobPostingPage = { "items": Array<JobPosting>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type JobStoreCard = { "id": string; "name": string; "industry": Industry; "address": string; "neighborhood": string }
export type Name = string
export type Notification = { "id": string; "type": "NEW_APPLICATION" | "INVITATION_ACCEPTED" | "STORE_APPROVED" | "INVITATION_EXPIRED" | "WORK_REQUEST_RECEIVED" | "WORK_REQUEST_NO_RESPONSE" | "WORK_CONFIRMED" | "STORE_INVITED" | "WORK_REMINDER" | "MANUAL_PUBLISHED" | "WORK_REQUEST_WITHDRAWN" | "WORK_CONFIRMATION_WITHDRAWN"; "title": string; "body": string; "target": NotificationTarget; "createdAt": string; "readAt": string | null }
export type NotificationPage = { "items": Array<Notification>; "page": number; "size": number; "totalItems": number; "asOf": string; "unreadCount": number }
export type NotificationRead = {  }
export type NotificationTarget = ({ "type": "JOB_APPLICATION"; "applicationId": string; "storeId": string; "jobId": string } | { "type": "WORK_REQUEST"; "requestId": string; "storeId": string; "jobId": string } | { "type": "STORE_INVITATION"; "invitationId": string } | { "type": "STORE"; "storeId": string } | { "type": "MANUAL"; "storeId": string } | { "type": "WORK_SCHEDULE"; "eventId": string; "storeId": string; "workDate": string })
export type OwnerHome = { "name": Name; "stores": Array<OwnerStore>; "selectedStoreId": string | null; "recruitingCount": number; "recruitingJobs": Array<JobPosting>; "unreadNotificationCount": number; "asOf": string }
export type OwnerJobApplication = { "id": string; "jobId": string; "status": "APPLIED" | "REQUESTED" | "CONFIRMED" | "WITHDRAWN" | "NOT_SELECTED" | "COMPLETED"; "introduction": string; "applicant": ApplicantSnapshot; "submittedAt": string; "revision": number }
export type OwnerJobApplicationPage = { "items": Array<OwnerJobApplication>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type OwnerStore = { "name": string; "industry": Industry; "postalCode": string; "address": string; "detailAddress"?: string; "businessRegistrationNumber": string; "phoneNumber": string; "id": string; "approvalRequestId": string; "approvalStatus": "PENDING" | "APPROVED"; "createdAt": string; "approvedAt": string | null; "permissions": Array<"READ_STORE_STATUS" | "MANAGE_STORE" | "INVITE_WORKERS" | "MANAGE_JOB_POSTINGS" | "MANAGE_MANUALS"> } & ({ "approvalStatus": "PENDING"; "approvedAt": null; "permissions": unknown } | { "approvalStatus": "APPROVED"; "approvedAt": string; "permissions": unknown })
export type OwnerStoreManagementSummary = { "storeId": string; "pendingInvitationCount": number; "activeWorkerCount": number; "expiringWorkerCount": number; "asOf": string }
export type OwnerStorePage = { "items": Array<OwnerStore>; "page": number; "size": number; "totalItems": number; "totalPages": number; "asOf": string }
export type PhoneNumber = string
export type ReceivedInvitation = { "id": string; "store": JobStoreCard; "status": "PENDING" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "CANCELLED"; "expiresAt": string; "accessExpiresAt": string | null; "createdAt": string }
export type ReceivedInvitationPage = { "items": Array<ReceivedInvitation>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type ReceivedInvitationResponse = { "decision": "ACCEPT" | "DECLINE" }
export type ReceivedInvitationResult = (StoreInvitationAcceptance | StoreInvitationDeclineResult)
export type RecommendedJobPosting = { "title": string; "description": string; "workPart": "WEEKDAY_OPEN" | "WEEKDAY_CLOSE" | "WEEKEND_OPEN" | "WEEKEND_CLOSE" | "OTHER"; "workDate": string; "startTime": string; "endTime": string; "endsNextDay": boolean; "minimumExperience": "ANY" | "MONTHS_3" | "MONTHS_6" | "YEAR_1"; "experienceNotes": string; "hourlyPay": number; "paymentTiming": "WORK_DAY" | "NEXT_DAY" | "NEGOTIABLE"; "paymentNotes": string; "id": string; "store": JobStoreCard; "status": "RECRUITING" | "CLOSED"; "recruitmentCount": 1; "applicantCount": number; "startAt": string; "endAt": string; "estimatedPay": number; "createdAt": string; "closedAt": string | null; "revision": number; "myApplicationId": string | null; "canApply": boolean; "cannotApplyReason": ("JOB_CLOSED" | "JOB_STARTED" | "ALREADY_APPLIED" | "ALREADY_CONFIRMED" | "JOB_FILLED" | null); "matchesAvailability": boolean } & (unknown & unknown) & ({ "canApply": true; "status": "RECRUITING"; "cannotApplyReason": null; "myApplicationId": null } | { "canApply": false; "cannotApplyReason": "JOB_CLOSED" | "JOB_STARTED" | "ALREADY_APPLIED" | "ALREADY_CONFIRMED" | "JOB_FILLED" })
export type StoreAccessGrant = { "id": string; "type": "REGULAR" | "TEMPORARY"; "dutyLabel": string | null; "startedAt": string; "validUntil": string | null; "revokedAt": string | null; "status": "ACTIVE" | "EXPIRING" | "EXPIRED" | "REVOKED"; "permissions": Array<"READ_MANUALS" | "READ_CHECKLISTS" | "USE_AI_QA"> } & ({ "type": "REGULAR"; "status": "ACTIVE"; "revokedAt": null; "permissions": unknown } | { "type": "REGULAR"; "status": "EXPIRING"; "revokedAt": null; "permissions": unknown; "validUntil": string } | { "type": "REGULAR"; "status": "EXPIRED"; "revokedAt": null; "permissions": unknown; "validUntil": string } | { "type": "REGULAR"; "status": "REVOKED"; "revokedAt": string; "permissions": unknown } | { "type": "TEMPORARY"; "status": "ACTIVE"; "revokedAt": null; "permissions": unknown; "validUntil": string } | { "type": "TEMPORARY"; "status": "EXPIRING"; "revokedAt": null; "permissions": unknown; "validUntil": string } | { "type": "TEMPORARY"; "status": "EXPIRED"; "revokedAt": null; "permissions": unknown; "validUntil": string } | { "type": "TEMPORARY"; "status": "REVOKED"; "revokedAt": string; "permissions": unknown; "validUntil": string })
export type StoreInvitation = { "id": string; "storeId": string; "email": string; "status": "PENDING" | "ACCEPTED" | "DECLINED" | "CANCELLED" | "EXPIRED"; "createdAt": string; "lastSentAt": string; "expiresAt": string; "accessExpiresAt": string | null; "acceptedAt": string | null; "acceptedBy": { "workerId": string; "name": Name } | { "workerId": string; "name": Name }; "declinedAt": string | null; "canceledAt": string | null } & ({ "status": "PENDING"; "acceptedAt": null; "acceptedBy": null; "declinedAt": null; "canceledAt": null } | { "status": "ACCEPTED"; "acceptedAt": string; "acceptedBy": {  }; "declinedAt": null; "canceledAt": null } | { "status": "DECLINED"; "acceptedAt": null; "acceptedBy": null; "declinedAt": string; "canceledAt": null } | { "status": "CANCELLED"; "acceptedAt": null; "acceptedBy": null; "declinedAt": null; "canceledAt": string } | { "status": "EXPIRED"; "acceptedAt": null; "acceptedBy": null; "declinedAt": null; "canceledAt": null })
export type StoreInvitationAcceptance = { "invitationId": string; "storeId": string; "workerId": string; "accessGrant": StoreAccessGrant }
export type StoreInvitationCreateRequest = { "email": string; "accessExpiresAt"?: string | null }
export type StoreInvitationDeclineResult = { "invitationId": string; "status": "DECLINED"; "declinedAt": string }
export type StoreInvitationDelivery = { "invitation": StoreInvitation; "deliveryStatus": "QUEUED" }
export type StoreInvitationPage = { "items": Array<StoreInvitation>; "page": number; "size": number; "totalItems": number; "totalPages": number; "asOf": string; "activeCount": number; "pastCount": number }
export type StoreInvitationPreview = { "invitationId": string; "store": { "id": string; "name": string }; "status": "PENDING"; "expiresAt": string; "accessType": "REGULAR"; "accessExpiresAt": string | null }
export type StoreInvitationTokenRequest = { "token": string }
export type StoreRegistrationInput = { "name": string; "industry": Industry; "postalCode": string; "address": string; "detailAddress"?: string; "businessRegistrationNumber": string; "phoneNumber": string }
export type StoreWorkerAccess = { "workerId": string; "name": Name; "storeId": string; "accessStatus": "ACTIVE" | "EXPIRING" | "ENDED"; "accessGrants": Array<StoreAccessGrant>; "permissions": Array<"READ_MANUALS" | "READ_CHECKLISTS" | "USE_AI_QA">; "asOf": string } & ({ "accessStatus": "ACTIVE"; "accessGrants": unknown; "permissions": unknown } | { "accessStatus": "EXPIRING"; "accessGrants": unknown; "permissions": unknown } | { "accessStatus": "ENDED"; "accessGrants": unknown; "permissions": unknown })
export type StoreWorkerAccessPage = { "items": Array<StoreWorkerAccess>; "page": number; "size": number; "totalItems": number; "totalPages": number; "asOf": string; "activeCount": number; "expiringCount": number; "endedCount": number }
export type WorkRequest = { "id": string; "jobId": string; "applicationId": string; "workerId": string; "workerName": Name; "status": "PENDING" | "ACCEPTED" | "DECLINED" | "EXPIRED" | "CANCELLED" | "CONFIRMATION_WITHDRAWN"; "requestedAt": string; "expiresAt": string; "respondedAt": string | null; "endedAt": string | null; "revision": number; "accessGrant": (StoreAccessGrant | null) } & ({ "status": "PENDING"; "respondedAt": null; "endedAt": null; "accessGrant": null } | { "status": "ACCEPTED"; "respondedAt": string; "endedAt": string; "accessGrant": StoreAccessGrant } | { "status": "DECLINED"; "respondedAt": string; "endedAt": string; "accessGrant": null } | { "status": "EXPIRED" | "CANCELLED"; "respondedAt": null; "endedAt": string; "accessGrant": null } | { "status": "CONFIRMATION_WITHDRAWN"; "respondedAt": string; "endedAt": string; "accessGrant": StoreAccessGrant })
export type WorkRequestCreate = { "expectedJobRevision": number }
export type WorkRequestPage = { "items": Array<WorkRequest>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type WorkRequestResponse = { "expectedRevision": number; "decision": "ACCEPT" | "DECLINE" }
export type WorkRequestWithdrawal = { "expectedRevision": number }
export type WorkerAccessibleStore = { "store": JobStoreCard; "access": StoreWorkerAccess; "publishedVersionId": string | null }
export type WorkerAccessibleStorePage = { "items": Array<WorkerAccessibleStore>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type WorkerAvailabilitiesReplaceRequest = { "availabilities": Array<Availability> }
export type WorkerBasicProfileUpdateRequest = { "name"?: Name; "phoneNumber"?: PhoneNumber; "birthDate"?: string; "gender"?: "MALE" | "FEMALE" }
export type WorkerCareersReplaceRequest = { "experienceLevel": "NEW" | "EXPERIENCED"; "careers": Array<Career> } & ({ "experienceLevel": "NEW"; "careers": unknown } | { "experienceLevel": "EXPERIENCED"; "careers": unknown })
export type WorkerHome = { "name": Name; "pendingApplicationCount": number; "favoriteStoreCount": number; "regularStoreCount": number; "unreadNotificationCount": number; "recommendedJobs": Array<RecommendedJobPosting>; "asOf": string }
export type WorkerJobPosting = { "title": string; "description": string; "workPart": "WEEKDAY_OPEN" | "WEEKDAY_CLOSE" | "WEEKEND_OPEN" | "WEEKEND_CLOSE" | "OTHER"; "workDate": string; "startTime": string; "endTime": string; "endsNextDay": boolean; "minimumExperience": "ANY" | "MONTHS_3" | "MONTHS_6" | "YEAR_1"; "experienceNotes": string; "hourlyPay": number; "paymentTiming": "WORK_DAY" | "NEXT_DAY" | "NEGOTIABLE"; "paymentNotes": string; "id": string; "store": JobStoreCard; "status": "RECRUITING" | "CLOSED"; "recruitmentCount": 1; "applicantCount": number; "startAt": string; "endAt": string; "estimatedPay": number; "createdAt": string; "closedAt": string | null; "revision": number; "myApplicationId": string | null; "canApply": boolean; "cannotApplyReason": ("JOB_CLOSED" | "JOB_STARTED" | "ALREADY_APPLIED" | "ALREADY_CONFIRMED" | "JOB_FILLED" | null) } & (({ "status": "RECRUITING"; "closedAt": null } | { "status": "CLOSED"; "closedAt": string }) & unknown & unknown) & ({ "canApply": true; "status": "RECRUITING"; "cannotApplyReason": null; "myApplicationId": null } | { "canApply": false; "cannotApplyReason": "JOB_CLOSED" | "JOB_STARTED" | "ALREADY_APPLIED" | "ALREADY_CONFIRMED" | "JOB_FILLED" })
export type WorkerJobPostingPage = { "items": Array<WorkerJobPosting>; "page": number; "size": number; "totalItems": number; "asOf": string }
export type WorkerProfile = { "name": Name; "phoneNumber": PhoneNumber; "birthDate": string; "gender": "MALE" | "FEMALE"; "experienceLevel": "NEW" | "EXPERIENCED"; "careers": Array<Career>; "availabilities": Array<Availability>; "id": string; "role": "WORKER"; "identity": GoogleIdentity; "updatedAt": string } & ({ "experienceLevel": "NEW"; "careers": unknown } | { "experienceLevel": "EXPERIENCED"; "careers": unknown })
export type Operations = {
 getMyWorkerProfile: {input: undefined; output: WorkerProfile}
 updateMyWorkerBasicProfile: {input: WorkerBasicProfileUpdateRequest; output: WorkerProfile}
 replaceMyWorkerCareers: {input: WorkerCareersReplaceRequest; output: WorkerProfile}
 replaceMyWorkerAvailabilities: {input: WorkerAvailabilitiesReplaceRequest; output: WorkerProfile}
 listMyOwnerStores: {input: undefined; output: OwnerStorePage}
 getMyOwnerStore: {input: undefined; output: OwnerStore}
 createMyOwnerStore: {input: StoreRegistrationInput; output: OwnerStore}
 createStoreInvitation: {input: StoreInvitationCreateRequest; output: StoreInvitationDelivery}
 listStoreInvitations: {input: undefined; output: StoreInvitationPage}
 resendStoreInvitation: {input: undefined; output: StoreInvitationDelivery}
 cancelStoreInvitation: {input: undefined; output: StoreInvitation}
 previewStoreInvitation: {input: StoreInvitationTokenRequest; output: StoreInvitationPreview}
 acceptStoreInvitation: {input: StoreInvitationTokenRequest; output: StoreInvitationAcceptance}
 declineStoreInvitation: {input: StoreInvitationTokenRequest; output: StoreInvitationDeclineResult}
 listStoreWorkers: {input: undefined; output: StoreWorkerAccessPage}
 getStoreWorker: {input: undefined; output: StoreWorkerAccess}
 revokeStoreWorkerAccess: {input: undefined; output: void}
 getStoreManagementSummary: {input: undefined; output: OwnerStoreManagementSummary}
 getOwnerJobPosting: {input: undefined; output: JobPosting}
 closeJobPosting: {input: JobClosure; output: JobPosting}
 createJobPosting: {input: JobPostingCreate; output: JobPosting}
 listOwnerJobPostings: {input: undefined; output: JobPostingPage}
 searchJobPostings: {input: undefined; output: WorkerJobPostingPage}
 getJobPosting: {input: undefined; output: WorkerJobPosting}
 getMyJobApplication: {input: undefined; output: JobApplication}
 withdrawJobApplication: {input: ApplicationRevision; output: JobApplication}
 applyForJobPosting: {input: ApplicationCreate; output: JobApplication}
 listMyJobApplications: {input: undefined; output: JobApplicationPage}
 listJobApplicants: {input: undefined; output: OwnerJobApplicationPage}
 getJobApplicant: {input: undefined; output: OwnerJobApplication}
 getMyWorkRequest: {input: undefined; output: WorkRequest}
 listMyWorkRequests: {input: undefined; output: WorkRequestPage}
 listOwnerWorkRequests: {input: undefined; output: WorkRequestPage}
 getConfirmedWorkerOnboarding: {input: undefined; output: JobOnboarding}
 respondToWorkRequest: {input: WorkRequestResponse; output: WorkRequest}
 requestApplicantWork: {input: WorkRequestCreate; output: WorkRequest}
 getReceivedStoreInvitation: {input: undefined; output: ReceivedInvitation}
 listReceivedStoreInvitations: {input: undefined; output: ReceivedInvitationPage}
 respondToReceivedStoreInvitation: {input: ReceivedInvitationResponse; output: ReceivedInvitationResult}
 markNotificationRead: {input: NotificationRead; output: Notification}
 listMyNotifications: {input: undefined; output: NotificationPage}
 listMyAccessibleStores: {input: undefined; output: WorkerAccessibleStorePage}
 getMyStoreAccess: {input: undefined; output: WorkerAccessibleStore}
 getMyWorkCalendarMonth: {input: undefined; output: CalendarMonth}
 saveFavoriteStore: {input: FavoriteStoreSave; output: FavoriteStore}
 removeFavoriteStore: {input: undefined; output: void}
 listMyFavoriteStores: {input: undefined; output: FavoriteStorePage}
 getOwnerHome: {input: undefined; output: OwnerHome}
 getWorkerHome: {input: undefined; output: WorkerHome}
 withdrawOwnerWorkRequest: {input: WorkRequestWithdrawal; output: WorkRequest}
 withdrawWorkConfirmation: {input: WorkRequestWithdrawal; output: WorkRequest}
 getOwnerWorkCalendarMonth: {input: undefined; output: CalendarMonth}
}
