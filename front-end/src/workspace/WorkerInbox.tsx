import {useMemo,useState} from 'react'
import {call,mutation} from '../api/operations'
import type {WorkRequest} from '../api/types.generated'
import {useCommand} from '../async/useCommand'
import {Resource} from './Resource'
import {InvitationAccept} from '../invitation/InvitationForms'
import {invitationApi} from '../invitation/api'
import {seoulDate} from '../home/api'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
import type {Route} from './Jobs'
export function ReceivedInvitation({id,route}:{id:string;route:Route}){const load=useMemo(()=>(signal:AbortSignal)=>call('getReceivedStoreInvitation',{signal,params:{invitationId:id}}),[id]),service=useMemo(()=>invitationApi('',''),[]);return <Resource load={load} onBack={()=>route('home')}>{i=><InvitationAccept invitation={{id:i.id,email:'',storeName:i.store.name,sentAt:seoulDate(i.createdAt),expiresAt:seoulDate(i.expiresAt),accessUntil:i.accessExpiresAt?seoulDate(i.accessExpiresAt):'별도 종료 전까지',status:i.status}} service={service} onBack={()=>route('home')} onResponded={()=>{}}/>}</Resource>}
export function WorkerRequest({id,route}:{id:string;route:Route}){const load=useMemo(()=>async(signal:AbortSignal)=>{const request=await call('getMyWorkRequest',{signal,params:{requestId:id}});const job=await call('getJobPosting',{signal,params:{jobId:request.jobId}});return {request,job}},[id]);return <Resource load={load} onBack={()=>route('home')}>{({request,job})=><RequestResponse initial={request} title={`${job.store.name} · ${job.title}`} onBack={()=>route('home')}/>}</Resource>}
function RequestResponse({initial,title,onBack}:{initial:WorkRequest;title:string;onBack:()=>void}){const [request,setRequest]=useState(initial),[decision,setDecision]=useState<'ACCEPT'|'DECLINE'|null>(null),write=useMemo(()=>mutation(),[]),{busy,run,cancel}=useCommand();const labels={PENDING:'응답 대기',ACCEPTED:'근무 확정',DECLINED:'거절 완료',EXPIRED:'기한 만료',CANCELLED:'요청 취소',CONFIRMATION_WITHDRAWN:'근무 확정 철회'};return <><MobileLayout header={<AppBar title="근무 요청" onBack={onBack}/>}><div className="home-content"><h2>{title}</h2><p>{labels[request.status]}</p><p>응답 기한: {new Date(request.expiresAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}</p>{request.status==='PENDING'&&<><Button onClick={()=>setDecision('ACCEPT')}>근무 수락</Button><Button intent="secondary" onClick={()=>setDecision('DECLINE')}>거절</Button></>}</div></MobileLayout><Modal description="처리 후 최신 근무 상태를 확인해 주세요." open={decision!==null} title={decision==='ACCEPT'?'근무를 수락할까요?':'근무 요청을 거절할까요?'} busy={busy} onClose={()=>{cancel();setDecision(null)}} onConfirm={async()=>{if(!decision)return;const ok=await run(signal=>write('respondToWorkRequest',{signal,params:{requestId:request.id},input:{expectedRevision:request.revision,decision}}),value=>{setRequest(value);setDecision(null)});if(!ok)throw Error('RESPONSE_FAILED')}}/></>}
