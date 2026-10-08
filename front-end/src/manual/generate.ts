import {ManualError,newKey,type ManualService} from './service'
import type {ManualDraftGenerationInput,ManualInterviewSession} from './types'

/** Fresh server snapshot, then immutable replay if the completion response is lost. */
export function createDraftGeneration(service:ManualService,sessionId:string,versionId:string) {
 const key=newKey();let body:ManualDraftGenerationInput|undefined
 return async(signal:AbortSignal):Promise<ManualInterviewSession>=>{
  if(!body){
   const [session,reviews]=await Promise.all([service.call('getManualInterview',{sessionId},undefined,{signal}),service.call('listManualIntentReviews',{sessionId},undefined,{signal})])
   const s=session.data,r=reviews.data
   if(s.id!==sessionId||r.sessionId!==sessionId||s.draftVersionId!==versionId)throw new ManualError('MANUAL_VERSION_CONFLICT')
   if(s.phase!=='READY_TO_GENERATE'||s.intents.some(i=>!i.finishedAt))throw new ManualError('INTERVIEW_INCOMPLETE')
   if(r.sessionRevision!==s.revision)throw new ManualError('REVISION_CONFLICT')
   const ids=new Set(r.items.map(i=>i.intentId))
   if(ids.size!==r.items.length||r.items.length!==s.intents.length||s.intents.some(i=>!ids.has(i.id))||r.items.some(i=>i.status!=='READY'))throw new ManualError('REVIEW_NOT_READY')
   body={expectedRevision:r.sessionRevision,reviewRevisions:r.items.map(i=>({intentId:i.intentId,revision:i.revision}))}
  }
  const result=await service.call('generateManualDraft',{sessionId},body,{signal,key})
  if(result.data.id!==sessionId||result.data.draftVersionId!==versionId)throw new ManualError('INVALID_RESPONSE')
  return result.data
 }
}
