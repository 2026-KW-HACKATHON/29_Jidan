import {ManualError,type ManualService} from './service'
import {createVoiceSubmission,type Recording} from './voice'
import type {ManualDraft,ManualDraftCorrectionTarget,ManualPublishInput} from './types'

export function assertDraftTarget(draft:ManualDraft,target:ManualDraftCorrectionTarget) {
 if(draft.generationStatus!=='READY'||!draft.content)throw new ManualError('MANUAL_STATE_CONFLICT')
 if(draft.latestCorrection?.status==='RUNNING')throw new ManualError('MANUAL_CORRECTION_IN_PROGRESS')
 if(target.kind==='SHIFT'&&!draft.content.shifts.some(s=>s.id===target.targetId)||target.kind==='SECTION'&&!draft.content.sections.some(s=>s.id===target.targetId))throw new ManualError('MANUAL_REFERENCE_CONFLICT')
}
export function createDraftVoiceCorrection(service:ManualService,draft:ManualDraft,target:ManualDraftCorrectionTarget,recording:Recording) {
 assertDraftTarget(draft,target)
 const submission=createVoiceSubmission(service,recording),versionId=draft.versionId,revision=draft.revision,capturedTarget=structuredClone(target)
 return async(signal:AbortSignal)=>{
  const input=await submission.input(signal)
  const result=await service.call('createManualDraftCorrection',{}, {expectedVersionId:versionId,expectedRevision:revision,target:capturedTarget,input},{signal,key:submission.key})
  if(result.data.versionId!==versionId||result.data.baseRevision!==revision||result.data.target.kind!==capturedTarget.kind||result.data.target.targetId!==capturedTarget.targetId)throw new ManualError('INVALID_RESPONSE')
  return result.data
 }
}
/** The clicked revision is never silently replaced with a freshly polled revision. */
export function publicationInput(draft:ManualDraft):ManualPublishInput {
 assertDraftTarget(draft,{kind:'MANUAL',targetId:null})
 if(draft.issues.some(issue=>issue.status!=='ACKNOWLEDGED'))throw new ManualError('MANUAL_ISSUES_NOT_ACKNOWLEDGED')
 return {expectedVersionId:draft.versionId,expectedRevision:draft.revision,confirmed:true,acknowledgedIssueIds:draft.issues.map(i=>i.id)}
}
