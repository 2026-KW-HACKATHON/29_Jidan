import {useState} from 'react'
import {Modal} from '../../ui/Modal'
import {useCommand} from '../../async/useCommand'
import type {OwnerJob} from './model'
import {OwnerJobSummary} from './OwnerJobList'
export type CloseJobService={close:(jobId:string,signal:AbortSignal)=>Promise<OwnerJob>}
export function JobCloseFlow({job,service,onClose,onUpdated,onCompleted,initialComplete=false}:{job:OwnerJob;service:CloseJobService;onClose:()=>void;onUpdated:(job:OwnerJob)=>void;initialComplete?:boolean;onCompleted?:()=>void}){
 const [complete,setComplete]=useState(initialComplete),{busy,run,cancel}=useCommand()
 const close=()=>{cancel();if(complete&&onCompleted)onCompleted();else onClose()}
 async function confirm(){if(job.status!=='recruiting'){close();return;}const ok=await run(signal=>service.close(job.id,signal),updated=>{onUpdated(updated);setComplete(true)});if(!ok)throw Error('CLOSE_FAILED')}
 return <Modal key={complete?'complete':'confirm'} state={complete?'information':'warning'} open closeOnConfirm={complete} className="owner-job-close-dialog" showIcon={false} title={complete?'모집을 마감했어요':'모집을 마감할까요?'} summary={complete?undefined:<OwnerJobSummary job={job}/>} description={complete?'이 공고는 더 이상 새로운 지원을 받지 않아요.':'지원자를 선정하지 않아도 마감할 수 있어요. 마감하면 새로운 지원을 받지 않아요.'} showCancel={!complete} confirmLabel={complete?'공고 목록 보기':'모집 마감'} busy={busy} onClose={close} onConfirm={complete?undefined:confirm}/>
}
