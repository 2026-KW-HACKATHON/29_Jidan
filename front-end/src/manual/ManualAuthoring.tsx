import {LoadingState} from '../ui/LoadingState'
import {ManualErrorDialog} from './ManualErrorDialog'
import {useEffect,useRef,useState} from 'react'
import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualInterview} from './ManualInterview'
import {ManualDraftReview} from './ManualDraftReview'
import {createDraftGeneration} from './generate'
import {useManualTask} from './useManualTask'
import {errorMessage,ManualError,type ManualService} from './service'
import type {ManualInterviewSession,ManualState} from './types'

export function ManualAuthoring(props:{service:ManualService;onBack:()=>void}) {
 return <ManualAuthoringStore key={props.service.storeId} {...props}/>
}
function ManualAuthoringStore({service,onBack}:{service:ManualService;onBack:()=>void}) {
 const [state,setState]=useState<ManualState|null>(null),[session,setSession]=useState<ManualInterviewSession|null>(null),[versionId,setVersionId]=useState<string|null>(null),[error,setError]=useState(''),[loaded,setLoaded]=useState(false),[refresh,setRefresh]=useState(0)
 const task=useManualTask(),generation=useRef<ReturnType<typeof createDraftGeneration>|null>(null)
 useEffect(()=>{
  const controller=new AbortController(),signal=controller.signal
  void (async()=>{
   const result=await service.call('getOwnerManualState',{},undefined,{signal});signal.throwIfAborted();setState(result.data)
   if(result.data.interviewSessionId){
    const interview=await service.call('getManualInterview',{sessionId:result.data.interviewSessionId},undefined,{signal});signal.throwIfAborted()
    if(interview.data.id!==result.data.interviewSessionId)throw new ManualError('INVALID_RESPONSE')
    if(['GENERATING','COMPLETED'].includes(interview.data.phase)||interview.data.phase==='ERROR'&&interview.data.processing?.kind==='DRAFT_GENERATION')setVersionId(interview.data.draftVersionId)
    else setSession(interview.data)
   }else if(result.data.draftVersionId)setVersionId(result.data.draftVersionId)
   setError('')
  })().catch(e=>{if(!signal.aborted)setError(errorMessage(e))}).finally(()=>{if(!signal.aborted)setLoaded(true)})
  return()=>controller.abort()
 },[service,refresh])
 function reload(){setLoaded(false);setState(null);setSession(null);setVersionId(null);generation.current=null;task.clearError();setRefresh(v=>v+1)}
 function start(){void task.run(async(signal,key)=>{const result=await service.call('startManualInterview',{}, {},{signal,key});signal.throwIfAborted();setSession(result.data)})}
 function finish(captured:ManualInterviewSession){generation.current=createDraftGeneration(service,captured.id,captured.draftVersionId);const generate=generation.current;void task.run(async(signal)=>{const result=await generate(signal);signal.throwIfAborted();setVersionId(result.draftVersionId)})}
 if(!loaded)return <ManualFrame onBack={onBack}><LoadingState message="작성 상태를 불러오고 있어요." cards/></ManualFrame>
 if(versionId)return <ManualDraftReview key={versionId} versionId={versionId} service={service} onBack={onBack} onReload={reload}/>
 if(session)return <><ManualErrorDialog error={task.error} onClose={task.clearError} onRetry={task.canRetry?task.retry:undefined} onReload={reload} retryLabel="생성 요청 다시 시도"/><ManualInterview key={session.id} initial={session} service={service} onBack={onBack} onFinal={finish} onDraft={setVersionId}/></>
 return <ManualFrame title="매뉴얼 만들기" onBack={onBack} footer={<Button busy={task.busy} disabled={!state} onClick={start}>인터뷰 시작하기</Button>}><div className="manual-stack"><h2 className="manual-heading">우리 매장 운영을<br/>하나씩 알려주세요</h2><p className="manual-muted">질문에 답하면 필요한 내용을 더 여쭤봐요.<br/>말해서 편하게 답해 주세요.</p><ManualCard title="이 순서로 함께 정리해요"><p>1. 근무조와 근무 시간<br/>2. 모두가 하는 공통 업무<br/>3. 근무조별로 다른 업무<br/>4. 빠진 규정과 필요한 사진</p></ManualCard><ManualCard title="정리한 내용은 점주님이 확인해요"><p>주제마다 이해한 내용을 보여드려요.<br/>다르게 정리됐다면 바로 고칠 수 있어요.</p></ManualCard><p className="manual-muted">마지막에 전체 내용을 확인하고<br/>근무자에게 공유할 수 있어요.</p><ManualErrorDialog error={error||task.error} onClose={error?reload:task.clearError} onRetry={task.canRetry?task.retry:undefined} onReload={reload} retryLabel="시작 요청 다시 시도"/></div></ManualFrame>
}
