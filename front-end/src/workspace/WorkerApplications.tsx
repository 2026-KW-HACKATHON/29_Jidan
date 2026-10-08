import {useEffect,useState} from 'react'
import {call,pages} from '../api/operations'
import type {JobApplication} from '../api/types.generated'
import {UserApplyStatusPage,type UserApplicationTab,type ApplicationListItem} from '../jobs/user/UserApplyStatusPage'
import type {Route} from './Jobs'
const serverTabs={APPLYING:'PENDING',CONFIRMED:'CONFIRMED',ENDED:'ENDED'} as const
const badges={APPLIED:'신청 중',REQUESTED:'근무 요청 도착',CONFIRMED:'확정',WITHDRAWN:'신청 취소',NOT_SELECTED:'미선정',COMPLETED:'근무 완료'} as const
function item(a:JobApplication):ApplicationListItem{
 const [year,month,day]=a.job.workDate.split('-');void year
 return {id:a.id,title:a.job.title,desc:a.job.store.name,time:`${Number(month)}월 ${Number(day)}일 ㅣ ${a.job.startTime} – ${a.job.endsNextDay?'다음 날 ':''}${a.job.endTime}`,badgeText:badges[a.status],badgeType:a.status==='CONFIRMED'?'green':a.status==='APPLIED'||a.status==='REQUESTED'?'blue':'gray'}
}
export function WorkerApplications({route}:{route:Route}){
 const [tab,setTab]=useState<UserApplicationTab>('APPLYING'),[attempt,setAttempt]=useState(0)
 const [counts,setCounts]=useState({pending:0,confirmed:0,ended:0})
 const [result,setResult]=useState<{tab:UserApplicationTab;items:ApplicationListItem[];attempt:number;error:boolean}|null>(null)
 useEffect(()=>{
  const controller=new AbortController(),signal=controller.signal
  async function load(){
   const first=await call('listMyJobApplications',{signal,query:{page:0,size:100,tab:serverTabs[tab]}})
   const items=await pages(page=>page===0?Promise.resolve(first):call('listMyJobApplications',{signal,query:{page,size:100,tab:serverTabs[tab]}}),signal)
   if(signal.aborted)return
   setCounts(first.counts);setResult({tab,items:items.map(item),attempt,error:false})
  }
  void load().catch(()=>{if(!signal.aborted)setResult({tab,attempt,items:[],error:true})})
  return()=>controller.abort()
 },[tab,attempt])
 const current=result?.tab===tab&&result.attempt===attempt?result:null
 return <UserApplyStatusPage tab={tab} counts={counts} lists={{APPLYING:[],CONFIRMED:[],ENDED:[],[tab]:current?.items||[]}} onTabChange={setTab} onBack={()=>route('home')} onOpen={id=>route('application',String(id))} loading={!current} error={current?.error?'신청 내역을 불러오지 못했어요. 접근 권한이나 연결 상태를 확인해 주세요.':undefined} onRetry={()=>setAttempt(v=>v+1)}/>
}
