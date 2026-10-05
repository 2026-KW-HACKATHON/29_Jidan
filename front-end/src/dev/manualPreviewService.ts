import type { ManualService,Operation,RequestOptions,Result } from '../manual/service'
import { ManualError } from '../manual/service'
import type { Operations,ManualInterviewSession } from '../manual/types'
import { interviewFixture,manualStoreId,stateFixture } from './manualFixtures'
export function createManualPreviewService(resume=false):ManualService {
 let session:ManualInterviewSession=structuredClone(interviewFixture)
 let started=resume
 const results=new Map<string,unknown>(),transcriptions=new Map<string,string>()
 return {storeId:manualStoreId,async call<N extends Operation>(name:N,_params:Record<string,string>,input:Operations[N]['input'],options:RequestOptions):Promise<Result<Operations[N]['output']>>{
  options.signal.throwIfAborted()
  if(options.key&&results.has(options.key))return {data:structuredClone(results.get(options.key)) as Operations[N]['output'],retryAfterMs:2000}
  let data:unknown
  switch(name){
   case 'getOwnerManualState':data={...stateFixture,interviewSessionId:started?session.id:null,draftVersionId:started?session.draftVersionId:null};break
   case 'startManualInterview':started=true;data=session;break
   case 'getManualInterview':data=session;break
   case 'uploadManualMedia':data={id:crypto.randomUUID(),storeId:manualStoreId,purpose:(input as FormData).get('purpose'),mimeType:'audio/webm',sizeBytes:1000,createdAt:new Date().toISOString()};break
   case 'createManualTranscription':{const id=crypto.randomUUID();transcriptions.set(id,(input as {mediaId:string}).mediaId);data={id,mediaId:(input as {mediaId:string}).mediaId,status:'RUNNING',text:null,error:null,createdAt:new Date().toISOString(),completedAt:null};break}
   case 'getManualTranscription':data={id:_params.transcriptionId,mediaId:transcriptions.get(_params.transcriptionId),status:'READY',text:'샘플 음성 답변',error:null,createdAt:new Date().toISOString(),completedAt:new Date().toISOString()};break
   case 'answerManualInterviewQuestion':session={...session,revision:session.revision+1,status:'IN_PROGRESS',error:null,completedAt:null,phase:'PROCESSING',questions:[],processing:{taskId:crypto.randomUUID(),kind:'EVALUATION',attempt:1}};data=session;break
   default:throw new ManualError('PREVIEW_NOT_IMPLEMENTED')
  }
  if(options.key)results.set(options.key,structuredClone(data))
  return {data:structuredClone(data) as Operations[N]['output'],retryAfterMs:2000}
 }}
}
