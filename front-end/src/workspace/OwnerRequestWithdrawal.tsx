import {useMemo} from 'react'
import {mutation} from '../api/operations'
import type {JobPosting,WorkRequest} from '../api/types.generated'
import {useCommand} from '../async/useCommand'
import {Modal} from '../ui/Modal'
import {Button} from '../ui/Button'
export function OwnerRequestWithdrawal({job,request,storeId,onClose,onCompleted,onReload}:{job:JobPosting;request:WorkRequest;storeId:string;onClose:()=>void;onCompleted:()=>void;onReload:()=>void}){
 const write=useMemo(mutation,[]),{busy,failed,run,cancel}=useCommand()
 const close=()=>{cancel();onClose()}
 async function confirm(){
  if(request.status!=='PENDING')throw Error('REQUEST_NOT_ACTIVE')
  const ok=await run(signal=>write('withdrawOwnerWorkRequest',{signal,params:{storeId,jobId:job.id,requestId:request.id},input:{expectedRevision:request.revision}}),onCompleted)
  if(!ok)throw Error('WITHDRAWAL_FAILED')
 }
 return <Modal open state="warning" showIcon={false} showCancel closeOnConfirm={false} busy={busy} title="근무 요청을 철회할까요?" description="지원자가 이 요청에 응답할 수 없게 돼요." confirmLabel="요청 철회" onClose={close} onConfirm={confirm} summary={failed?<div><p>상태가 바뀌었을 수 있어요. 최신 상태를 확인하거나 다시 시도해 주세요.</p><Button intent="secondary" onClick={()=>{close();onReload()}}>최신 상태 확인</Button></div>:undefined}/>
}
