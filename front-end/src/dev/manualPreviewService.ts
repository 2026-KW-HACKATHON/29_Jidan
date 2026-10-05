import type { ManualService,Operation,RequestOptions,Result } from '../manual/service'
import {schemas} from '../manual/contract.generated'
import {matches} from '../manual/validation'
import { ManualError } from '../manual/service'
import type { Operations,ManualInterviewSession,ManualIntentReview,ManualInterviewAnswer,ManualInterviewCorrection } from '../manual/types'
import { interviewFixture,manualStoreId,stateFixture,reviewFixture } from './manualFixtures'
export function createManualPreviewService(resume=false,scenario=''):ManualService {
 const asSession=(value:unknown):ManualInterviewSession=>{if(!matches(schemas.ManualInterviewSession,value,schemas))throw new ManualError('INVALID_PREVIEW_SESSION');return value as ManualInterviewSession}
 let session:ManualInterviewSession=structuredClone(interviewFixture)
 let started=resume||!!scenario,count=0,sequence=0
 const reviews=new Map<string,ManualIntentReview>(),results=new Map<string,unknown>(),transcriptions=new Map<string,string>(),turns:Operations['getManualInterviewTurns']['output']['items']=[]
 const stages=['WORK_STRUCTURE','COMMON_TASKS','SHIFT_TASKS','COMPLEMENTS'] as const
 session.intents=stages.map((stage,i)=>({...session.intents[0],id:i===0?session.intents[0].id:crypto.randomUUID(),stage,key:['근무조와 시간','공통 업무','근무별 업무','규정·설비'][i],depth:0,coverage:'PENDING',finishedAt:null}))
 function makeReview(intentId:string){return {...structuredClone(reviewFixture),intentId,content:{...structuredClone(reviewFixture.content!),intentId}} as ManualIntentReview}
 function finishIntent(index:number){const intent=session.intents[index];if(!intent)return;session.intents[index]={...intent,...(scenario==='depth5'?{coverage:'NEEDS_DETAIL' as const,depth:5 as const,finishedAt:new Date().toISOString()}:{coverage:'COVERED' as const,depth:0,finishedAt:new Date().toISOString()})};reviews.set(intent.id,makeReview(intent.id))}
 if(scenario==='review'||scenario==='review-error'||scenario==='depth5'){finishIntent(0);session=asSession({...session,revision:session.revision+1,currentIntentId:session.intents[1].id,questions:[{...session.questions[0],id:crypto.randomUUID(),intentId:session.intents[1].id,text:'모두가 하는 일은 무엇인가요?'}]});if(scenario==='review-error'){const r=reviews.get(session.intents[0].id)!;reviews.set(r.intentId,{...r,status:'ERROR',confirmedAt:null,error:{code:'AI_PROCESSING_FAILED',message:'요약 실패',retryable:true},processing:{taskId:crypto.randomUUID(),kind:'UNDERSTANDING',attempt:1}})}}
 if(scenario==='ready'){session.intents.forEach((_,i)=>finishIntent(i));session=asSession({...session,status:'IN_PROGRESS',phase:'READY_TO_GENERATE',currentIntentId:null,questions:[],processing:null,error:null,completedAt:null})}
 return {storeId:manualStoreId,async call<N extends Operation>(name:N,params:Record<string,string>,input:Operations[N]['input'],options:RequestOptions):Promise<Result<Operations[N]['output']>>{
  options.signal.throwIfAborted()
  if(options.key&&results.has(options.key))return {data:structuredClone(results.get(options.key)) as Operations[N]['output'],retryAfterMs:2000}
  let data:unknown
  switch(name){
   case 'getOwnerManualState':data={...stateFixture,interviewSessionId:started?session.id:null,draftVersionId:started?session.draftVersionId:null};break
   case 'startManualInterview':started=true;data=session;break
   case 'getManualInterview':{
    if(session.phase==='PROCESSING'){
     const index=session.intents.findIndex(i=>i.id===session.currentIntentId),intent=session.intents[index]
     if(count%2===1)session=asSession({...session,status:'IN_PROGRESS',error:null,completedAt:null,phase:'COLLECTING',revision:session.revision+1,processing:null,questions:[{id:crypto.randomUUID(),intentId:intent.id,kind:'PROBE',depth:1,batchId:crypto.randomUUID(),text:'처음 일하는 근무자도 따라 할 수 있게 조금 더 알려주세요.',answered:false}]})
     else {finishIntent(index);const next=session.intents[index+1];session=asSession({...session,status:'IN_PROGRESS',error:null,completedAt:null,phase:next?'COLLECTING':'READY_TO_GENERATE',revision:session.revision+1,processing:null,currentIntentId:next?.id??null,questions:next?[{id:crypto.randomUUID(),intentId:next.id,kind:'BASE',depth:0,batchId:null,text:['','모두가 하는 일은 무엇인가요?','근무조별로 다른 일은 무엇인가요?','규정과 설비 사용 방법을 알려주세요.'][index+1],answered:false}]:[]})}
    }
    data=session;break
   }
   case 'uploadManualMedia':data={id:crypto.randomUUID(),storeId:manualStoreId,purpose:(input as FormData).get('purpose'),mimeType:'audio/webm',sizeBytes:1000,createdAt:new Date().toISOString()};break
   case 'createManualTranscription':{const id=crypto.randomUUID();transcriptions.set(id,(input as {mediaId:string}).mediaId);data={id,mediaId:(input as {mediaId:string}).mediaId,status:'RUNNING',text:null,error:null,createdAt:new Date().toISOString(),completedAt:null};break}
   case 'getManualTranscription':data={id:params.transcriptionId,mediaId:transcriptions.get(params.transcriptionId),status:'READY',text:'샘플 음성 답변',error:null,createdAt:new Date().toISOString(),completedAt:new Date().toISOString()};break
   case 'answerManualInterviewQuestion':{const answer=input as ManualInterviewAnswer;if(answer.expectedRevision!==session.revision||answer.questionId!==session.questions[0]?.id)throw new ManualError('REVISION_CONFLICT');count++;turns.push({id:crypto.randomUUID(),sequence:++sequence,intentId:session.currentIntentId!,kind:'ANSWER',speaker:'OWNER',questionKind:null,depth:session.questions[0].depth,batchId:null,replyToQuestionId:answer.questionId,inputMethod:'VOICE',content:'샘플 음성 답변',photoIds:[],createdAt:new Date().toISOString()});session=asSession({...session,revision:session.revision+1,status:'IN_PROGRESS',error:null,completedAt:null,phase:'PROCESSING',questions:[],processing:{taskId:crypto.randomUUID(),kind:'EVALUATION',attempt:1}});data=session;break}
   case 'listManualIntentReviews':data={sessionId:session.id,sessionRevision:session.revision,items:[...reviews.values()]};break
   case 'getManualIntentReview':data=reviews.get(params.intentId);break
   case 'correctManualInterviewUnderstanding':{const r=reviews.get(params.intentId)!,correction=input as ManualInterviewCorrection;if(correction.expectedRevision!==r.revision)throw new ManualError('REVISION_CONFLICT');data={...r,revision:r.revision+1,status:'READY',confirmedAt:null,content:{...r.content,summary:'수정한 내용을 반영했어요. 야간조는 오전 6시에 끝나요.'}};reviews.set(params.intentId,data as ManualIntentReview);break}
   case 'confirmManualInterviewUnderstanding':{const r=reviews.get(params.intentId)!;data={...r,revision:r.revision+1,confirmedAt:new Date().toISOString()};reviews.set(params.intentId,data as ManualIntentReview);break}
   case 'retryManualIntentReview':{const r=reviews.get(params.intentId)!;data={...r,revision:r.revision+1,status:'READY',error:null,processing:null};reviews.set(params.intentId,data as ManualIntentReview);break}
   case 'getManualInterviewTurns':data={items:turns,page:0,size:100,totalItems:turns.length,totalPages:turns.length?1:0,asOf:new Date().toISOString()};break
   default:throw new ManualError('PREVIEW_NOT_IMPLEMENTED')
  }
  if(options.key)results.set(options.key,structuredClone(data))
  return {data:structuredClone(data) as Operations[N]['output'],retryAfterMs:2000}
 }}
}
