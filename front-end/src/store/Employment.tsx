import { useEffect, useRef, useState } from 'react'
import { DeadlineExceeded, withDeadline } from '../async/deadline'
import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { WorkerInformation, PermissionCard } from './WorkerCards'
import './Employment.css'
export type EmploymentData={name:string;job:string;type:string;start:string;expiry:string;permissions:string[];status?:'active'|'expiring'|'ended'}
/** UI-only command. Server identity, route and response are intentionally unspecified. */
export type AccessService={end:(key:string,signal:AbortSignal)=>Promise<void>}
const unavailable:AccessService={end:async()=>{throw Error('ACCESS_NOT_CONFIGURED')}}
type Phase='warning'|'error'|'information'|null
export function Employment({data,onBack,service=unavailable,onEnded}: {data:EmploymentData;onBack:()=>void;service?:AccessService;onEnded?:()=>void}) {
 const [phase,setPhase]=useState<Phase>(null),[ended,setEnded]=useState(data.status==='ended'),[busy,setBusy]=useState(false)
 const request=useRef<AbortController|null>(null),lock=useRef(false),key=useRef(crypto.randomUUID()),trigger=useRef<HTMLButtonElement>(null),summary=useRef<HTMLHeadingElement>(null),currentPhase=useRef<Phase>(null)
 useEffect(()=>()=>request.current?.abort(),[])
 function transition(next:Phase) { currentPhase.current=next; setPhase(next) }
 function close(expected:Phase) {
   // Modal may complete its old confirmation after the next phase has started.
   if (currentPhase.current !== expected) return
   request.current?.abort(); lock.current=false; setBusy(false); transition(null)
   queueMicrotask(()=>{if(ended)summary.current?.focus();else trigger.current?.focus()})
 }
 async function end() {
   if(lock.current||ended)return
   lock.current=true;setBusy(true)
   const controller=new AbortController();request.current=controller
   try {
     await withDeadline(signal=>service.end(key.current,signal),controller)
     if(!controller.signal.aborted){setEnded(true);transition('information');onEnded?.()}
   } catch (failure) {
     if(!controller.signal.aborted||failure instanceof DeadlineExceeded)transition('error')
   } finally {
     if(request.current===controller&&(!controller.signal.aborted||controller.signal.reason instanceof DeadlineExceeded)){lock.current=false;setBusy(false)}
   }
 }
 const description=phase==='warning'?`${data.name} 님은 이 매장의 업무 매뉴얼,\n체크리스트, AI 질의응답을 이용할 수 없어요.`:phase==='error'?'일시적인 연결 문제로 변경 사항이\n저장되지 않았어요. 잠시 후 다시 시도해 주세요.':`${data.name} 님의 매장 접근 권한이 종료됐어요.\n변경된 상태는 근무자 목록에서 확인할 수 있어요.`
 return <><MobileLayout className="employment" header={<AppBar title="근무자 상세" onBack={onBack}/>}>
  <WorkerInformation data={data} ended={ended} summaryRef={summary}/>
  <PermissionCard permissions={data.permissions} ended={ended}/>
  <div className="employment-end"><p>접근을 종료하면 매장 업무 자료를<br/>더 이상 열람할 수 없어요.</p><Button ref={trigger} className="employment-end-button" disabled={ended} onClick={()=>transition('warning')}>{ended?'접근 종료 완료':'접근 종료 처리'}</Button></div>
 </MobileLayout><Modal key={phase} open={phase!==null} state={phase??'warning'} busy={busy} title={phase==='warning'?'매장 접근을 종료할까요?':phase==='error'?'요청을 처리하지 못했어요':'접근 종료가 완료됐어요'} description={description} confirmLabel={phase==='warning'?'접근 종료':phase==='error'?'다시 시도':'확인'} cancelLabel={phase==='warning'?'취소':'닫기'} onConfirm={phase==='information'?undefined:end} onClose={()=>close(phase)}/></>
}
