import { useEffect,useRef,useState } from 'react'
import { Button } from '../ui/Button'
import { ManualFrame,ManualCard } from './ManualFrame'
import { ManualVoiceComposer } from './ManualVoiceComposer'
import { createVoiceSubmission } from './voice'
import type { Recording } from './voice'
import { errorMessage,newKey } from './service'
import type { ManualService } from './service'
import type { ManualInterviewSession,ManualState } from './types'
export function ManualAuthoring({service,onBack}:{service:ManualService;onBack:()=>void}){
 const [state,setState]=useState<ManualState|null>(null),[session,setSession]=useState<ManualInterviewSession|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[refresh,setRefresh]=useState(0)
 const mounted=useRef(true),request=useRef<AbortController|null>(null),lock=useRef(false),startKey=useRef(newKey()),voice=useRef<{recording:Recording;submission:ReturnType<typeof createVoiceSubmission>}|null>(null)
 useEffect(()=>{mounted.current=true;const controller=new AbortController();request.current=controller
  void service.call('getOwnerManualState',{},undefined,{signal:controller.signal}).then(async result=>{if(controller.signal.aborted)return;setState(result.data);if(result.data.interviewSessionId){const s=await service.call('getManualInterview',{sessionId:result.data.interviewSessionId},undefined,{signal:controller.signal});if(!controller.signal.aborted)setSession(s.data)}}).catch(e=>{if(!controller.signal.aborted)setError(errorMessage(e))}).finally(()=>{if(!controller.signal.aborted)setLoaded(true)})
  return()=>{mounted.current=false;controller.abort();request.current?.abort()}
 },[service,refresh])
 async function start(){if(lock.current)return;lock.current=true;setBusy(true);const controller=new AbortController();request.current=controller;try{const result=await service.call('startManualInterview',{}, {},{signal:controller.signal,key:startKey.current});if(!controller.signal.aborted){setSession(result.data);setError('')}}catch(e){if(!controller.signal.aborted)setError(errorMessage(e))}finally{if(mounted.current){setBusy(false);lock.current=false}}}
 async function answer(recording:Recording,signal:AbortSignal){if(!session||!session.questions[0])return;const current=session,question=current.questions[0];if(voice.current?.recording!==recording)voice.current={recording,submission:createVoiceSubmission(service,recording)};const submission=voice.current.submission;const input=await submission.input(signal);signal.throwIfAborted();const result=await service.call('answerManualInterviewQuestion',{sessionId:current.id},{expectedRevision:current.revision,questionId:question.id,input},{signal,key:submission.key});if(!signal.aborted){setSession(result.data);voice.current=null}}
 const stage=session?.phase==='READY_TO_GENERATE'||session?.phase==='COMPLETED'?4:['WORK_STRUCTURE','COMMON_TASKS','SHIFT_TASKS','COMPLEMENTS'].indexOf(session?.intents.find(i=>i.id===session.currentIntentId)?.stage??'')
 if(!loaded)return <ManualFrame onBack={onBack}><p role="status">작성 상태를 불러오고 있어요.</p></ManualFrame>
 if(!session)return <ManualFrame title="매뉴얼 만들기" onBack={onBack} footer={<Button busy={busy} disabled={!state} onClick={()=>void start()}>인터뷰 시작하기</Button>}><div className="manual-stack"><h2 className="manual-heading">우리 매장 운영을<br/>하나씩 알려주세요</h2><p className="manual-muted">질문에 답하면 필요한 내용을 더 여쭤봐요.<br/>말해서 편하게 답해 주세요.</p><ManualCard title="이 순서로 함께 정리해요"><p>1. 근무조와 근무 시간<br/>2. 모두가 하는 공통 업무<br/>3. 근무조별로 다른 업무<br/>4. 빠진 규정과 필요한 사진</p></ManualCard><ManualCard title="정리한 내용은 점주님이 확인해요"><p>주제마다 이해한 내용을 보여드려요.<br/>다르게 정리됐다면 바로 고칠 수 있어요.</p></ManualCard><p className="manual-muted">마지막에 전체 내용을 확인하고<br/>근무자에게 공유할 수 있어요.</p>{error&&<><p role="alert">{error}</p><Button intent="secondary" onClick={()=>setRefresh(v=>v+1)}>다시 불러오기</Button></>}</div></ManualFrame>
 return <ManualFrame onBack={onBack} stage={stage} footer={session.phase==='COLLECTING'?<ManualVoiceComposer key={`${service.storeId}:${session.id}:${session.questions[0]?.id}`} onRecording={answer}/>:<p role="status">{session.phase==='COMPLETED'?'초안 작성이 완료됐어요.':session.phase==='READY_TO_GENERATE'?'모든 질문에 답했어요.':'답변을 정리하고 있어요.'}</p>}><div className="manual-stack">{session.questions[0]&&<h2 className="manual-heading manual-question">{session.questions[0].text}</h2>}{error&&<><p role="alert">{error}</p><Button intent="secondary" onClick={()=>setRefresh(v=>v+1)}>다시 불러오기</Button></>}</div></ManualFrame>
}
