import {useEffect,useRef,useState} from 'react'
import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {ManualContentView} from './ManualContentView'
import {errorMessage,ManualError} from './service'
import type {ManualService} from './service'
import type {ManualInterviewSession,ManualIntentReview,ManualIntentReviewList,ManualInterviewTurn} from './types'
import {createVoiceSubmission,type Recording} from './voice'
import {pause,waitVisible} from './poll'
import {useManualTask} from './useManualTask'
import {ManualReviewPhotos} from './ManualReviewPhotos'
import type {PhotoTarget} from './photos'
export function ManualInterview({service,initial,onBack,onFinal}:{service:ManualService;initial:ManualInterviewSession;onBack:()=>void;onFinal?:(session:ManualInterviewSession)=>void}){
 const [session,setSession]=useState(initial),[reviews,setReviews]=useState<ManualIntentReviewList|null>(null),[selected,setSelected]=useState<string|null>(null),[correcting,setCorrecting]=useState(false),[pollError,setPollError]=useState(''),[reviewError,setReviewError]=useState(''),[turns,setTurns]=useState<ManualInterviewTurn[]>([]),[showHistory,setShowHistory]=useState(false),[refresh,setRefresh]=useState(0)
 const [photoTarget,setPhotoTarget]=useState<PhotoTarget|null>(null)
 const task=useManualTask(),historyTask=useManualTask(),voice=useRef<{recording:Recording;submission:ReturnType<typeof createVoiceSubmission>;revision:number;questionId?:string;intentId?:string}|null>(null),current=useRef(session)
 useEffect(()=>{current.current=session},[session])
 useEffect(()=>{const controller=new AbortController(),signal=controller.signal
  void (async()=>{while(!signal.aborted){await waitVisible(signal);const results=await Promise.allSettled([service.call('getManualInterview',{sessionId:initial.id},undefined,{signal}),service.call('listManualIntentReviews',{sessionId:initial.id},undefined,{signal})]);if(signal.aborted)return
   const s=results[0],r=results[1];let delay=2000
   if(s.status==='fulfilled'){if(s.value.data.id!==initial.id)throw new ManualError('INVALID_RESPONSE');setSession(old=>s.value.data.revision>=old.revision?s.value.data:old);setPollError('');delay=s.value.retryAfterMs}else setPollError(errorMessage(s.reason))
   if(r.status==='fulfilled'){if(r.value.data.sessionId!==initial.id)throw new ManualError('INVALID_RESPONSE');setReviews(old=>({ ...r.value.data,items:r.value.data.items.map(item=>{const newer=old?.items.find(i=>i.intentId===item.intentId);return newer&&newer.revision>item.revision?newer:item})}));setReviewError('');delay=Math.max(delay,r.value.retryAfterMs)}else setReviewError(errorMessage(r.reason))
   await pause(delay,signal)
  }})().catch(e=>{if(!signal.aborted)setPollError(errorMessage(e))})
  return()=>controller.abort()
 },[service,initial.id,refresh])
 const review=reviews?.items.find(r=>r.intentId===selected),intent=session.intents.find(i=>i.id===(review?.intentId??session.currentIntentId)),stage=['WORK_STRUCTURE','COMMON_TASKS','SHIFT_TASKS','COMPLEMENTS'].indexOf(intent?.stage??'')
 function updateReview(updated:ManualIntentReview){setReviews(old=>old?{...old,items:old.items.map(r=>r.intentId===updated.intentId&&r.revision<=updated.revision?updated:r)}:old)}
 async function submit(recording:Recording,signal:AbortSignal){
  if(voice.current?.recording!==recording){voice.current={recording,submission:createVoiceSubmission(service,recording),revision:review?review.revision:session.revision,questionId:review?undefined:session.questions[0]?.id,intentId:review?.intentId}}
  const captured=voice.current;const input=await captured.submission.input(signal);signal.throwIfAborted()
  if(captured.intentId){const result=await service.call('correctManualInterviewUnderstanding',{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision,input},{signal,key:captured.submission.key});if(!signal.aborted){updateReview(result.data);setCorrecting(false)}}
  else {if(!captured.questionId)throw new ManualError('INTERVIEW_STATE_CONFLICT');const result=await service.call('answerManualInterviewQuestion',{sessionId:session.id},{expectedRevision:captured.revision,questionId:captured.questionId,input},{signal,key:captured.submission.key});if(!signal.aborted)setSession(old=>result.data.revision>=old.revision?result.data:old)}
  voice.current=null
 }
 function reviewCommand(kind:'confirmManualInterviewUnderstanding'|'retryManualIntentReview'){if(!review)return;const captured=review;void task.run(async(signal,key)=>{const result=kind==='confirmManualInterviewUnderstanding'?await service.call(kind,{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision,confirmed:true},{signal,key}):await service.call(kind,{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision},{signal,key});if(signal.aborted)return;updateReview(result.data);if(kind==='confirmManualInterviewUnderstanding')setSelected(null)})}
 async function loadHistory(){void historyTask.run(async(signal)=>{let page=0,total=1;const items=new Map<string,ManualInterviewTurn>();while(page<total){const result=await service.call('getManualInterviewTurns',{sessionId:session.id,page:String(page)},undefined,{signal});total=result.data.totalPages;result.data.items.forEach(turn=>items.set(turn.id,turn));page++}if(!signal.aborted){setTurns([...items.values()].sort((a,b)=>a.sequence-b.sequence));setShowHistory(true)}})}
 const ready=session.phase==='READY_TO_GENERATE'&&reviews?.items.length===session.intents.length&&reviews.items.every(r=>r.status==='READY')
 const footer=review?correcting?<ManualVoiceComposer key={review.intentId+':correction'} onRecording={submit} label="말해서 답하기"/>:<div className="manual-stack"><div className="manual-button-row"><Button intent="secondary" disabled={task.busy||review.status!=='READY'} onClick={()=>setCorrecting(true)}>수정할게요</Button><Button busy={task.busy} disabled={review.status!=='READY'} onClick={()=>reviewCommand('confirmManualInterviewUnderstanding')}>네, 맞아요</Button></div><Button intent="secondary" onClick={()=>{setSelected(null);setCorrecting(false)}}>나중에 확인하기</Button></div>:session.phase==='COLLECTING'?<ManualVoiceComposer key={session.questions[0]?.id} onRecording={submit}/>:session.phase==='ERROR'?<Button busy={task.busy} onClick={()=>void task.run(async(signal,key)=>{const result=await service.call('retryManualInterviewProcessing',{sessionId:session.id},{expectedRevision:session.revision},{signal,key});if(!signal.aborted)setSession(old=>result.data.revision>=old.revision?result.data:old)})}>답변 처리 다시 시도</Button>:session.phase==='READY_TO_GENERATE'?<Button disabled={!ready||!onFinal} onClick={()=>onFinal?.(current.current)}>최종 검토로</Button>:<p className="manual-voice-title" role="status">답변을 정리하고 있어요</p>
 if(photoTarget&&review)return <ManualReviewPhotos service={service} sessionId={session.id} review={review} target={photoTarget} onUpdate={updateReview} onClose={()=>setPhotoTarget(null)}/>
 return <ManualFrame onBack={onBack} stage={stage} footer={footer}><div className="manual-stack">
  {review?<><h2 className="manual-heading">{correcting?'어떤 부분을\n고치면 될까요?':'이렇게 이해했어요'}</h2><p className="manual-muted">{correcting?'다르게 정리된 내용을 말해 주세요.':'내용이 맞는지 확인해 주세요.'}</p>{review.content&&<><ManualCard title="정리한 내용"><p className="manual-question">{review.content.summary}</p></ManualCard><ManualContentView content={review.content} service={service}/></>}{review.status==='PROCESSING'&&<p role="status">요약을 정리하고 있어요.</p>}{review.status==='ERROR'&&<><p role="alert">요약을 정리하지 못했어요. 질문은 계속 답할 수 있어요.</p><Button busy={task.busy} onClick={()=>reviewCommand('retryManualIntentReview')}>요약 다시 시도</Button></>}{!correcting&&review.status==='READY'&&review.content&&<><Button intent="secondary" onClick={()=>setPhotoTarget({target:'WORK_STRUCTURE',sectionId:null})}>근무 구조 사진 첨부</Button>{review.content.sections.map(section=><Button key={section.id} intent="secondary" onClick={()=>setPhotoTarget({target:'SECTION',sectionId:section.id})}>{section.title} 사진 첨부</Button>)}</>}</>:<><h2 className="manual-heading">{session.questions[0]?.text??(session.phase==='READY_TO_GENERATE'?'모든 질문에 답했어요.':'답변을 정리하고 있어요')}</h2>{session.questions[0]?.kind==='PROBE'&&<p className="manual-muted">추가로 알려주세요. ({session.questions[0].depth}/5)</p>}</>}
  {session.intents.some(i=>i.coverage==='NEEDS_DETAIL')&&<p className="manual-muted">정보가 부족한 주제는 최종 검토에서 확인할 수 있어요.</p>}
  {!correcting&&reviews?.items.map(r=><Button key={r.intentId} intent="secondary" onClick={()=>{setSelected(r.intentId);setCorrecting(false);task.clearError()}}>정리한 내용 확인 · {session.intents.find(i=>i.id===r.intentId)?.key??'이전 주제'}{r.status==='ERROR'?' · 다시 시도 필요':''}</Button>)}
  {(pollError||reviewError)&&<><p role="alert">{pollError||reviewError}</p><Button intent="secondary" onClick={()=>setRefresh(v=>v+1)}>최신 내용 다시 불러오기</Button></>}{task.error&&<><p role="alert">{task.error}</p><Button intent="secondary" onClick={task.retry}>요청 다시 시도</Button></>}
  <Button intent="secondary" busy={historyTask.busy} onClick={()=>void loadHistory()}>질문·답변 이력</Button>{historyTask.error&&<p role="alert">{historyTask.error}</p>}{showHistory&&<ol className="manual-history">{turns.map(t=><li key={t.id}>{t.kind==='QUESTION'?t.content:t.kind==='CORRECTION'?'음성 수정 완료':'음성 답변 완료'}</li>)}</ol>}
 </div></ManualFrame>
}
