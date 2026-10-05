import {useState} from 'react'
import {Modal} from '../../ui/Modal'
import {useCommand} from '../../async/useCommand'
import {OwnerJobSummary} from './OwnerJobList'
import type {OwnerJob} from './model'
import type {JobApplicant} from './OwnerApplicants'

export type WorkRequestService={request:(jobId:string,applicantId:string,signal:AbortSignal)=>Promise<void>}
const unavailableWorkRequests:WorkRequestService={request:async()=>{throw Error('WORK_REQUEST_NOT_CONFIGURED')}}

export function WorkRequestFlow({job,applicant,service=unavailableWorkRequests,onClose,onCompleted,initialComplete=false}:{job:OwnerJob;applicant:JobApplicant;service?:WorkRequestService;onClose:()=>void;onCompleted:()=>void;initialComplete?:boolean}){
 const [complete,setComplete]=useState(initialComplete)
 const {busy,run,cancel}=useCommand()
 const close=()=>{cancel();if(complete)onCompleted();else onClose()}
 async function confirm(){
  if(job.status!=='recruiting')throw Error('JOB_NOT_RECRUITING')
  const ok=await run(signal=>service.request(job.id,applicant.id,signal),()=>setComplete(true))
  if(!ok)throw Error('WORK_REQUEST_FAILED')
 }
 return <Modal key={complete?'complete':'confirm'} open showIcon={false} showCancel={!complete} closeOnConfirm={complete}
  className="owner-work-request-dialog" title={complete?'요청을 완료했어요!':'근무 요청을 보낼까요?'}
  summary={complete?undefined:<div className="owner-work-request-summary"><h3>{applicant.name}</h3><p>{applicant.experience||'등록한 경력 없음'}</p><OwnerJobSummary job={job}/></div>}
  description={complete?'1시간 이내로 응답이 없으면 알려드릴게요':''}
  confirmLabel={complete?'공고 목록 보기':'요청하기'} busy={busy} onClose={close} onConfirm={complete?undefined:confirm}/>
}
