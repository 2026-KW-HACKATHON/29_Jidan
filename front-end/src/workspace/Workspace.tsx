import {useEffect,useMemo,useState} from 'react'
import type {Session} from '../auth/session'
import {call} from '../api/operations'
import {OwnerHome} from '../home/OwnerHome'
import {WorkerHome} from '../home/WorkerHome'
import {ownerHomeData,workerHomeData,seoulDate} from '../home/api'
import {WorkerProfile} from '../profile/WorkerProfile'
import {profileService} from '../profile/service'
import {ManualAuthoring} from '../manual/ManualAuthoring'
import {liveManualService} from '../manual/live'
import {WorkerQa} from '../qa/WorkerQa'
import {WorkerManual} from './WorkerManual'
import {OwnerJobs,WorkerJobs,type Route} from './Jobs'
import {StorePages} from './Stores'
import {InvitationPages} from './Invitations'
import {Resource} from './Resource'
import {pendingInvitation,clearInvitation} from '../invitation/link'
import {TokenInvitation} from './TokenInvitation'
import {AddStore} from './AddStore'
import {Notifications} from './Notifications'
import {WorkerApplications} from './WorkerApplications'
import {ReceivedInvitation,WorkerRequest} from './WorkerInbox'

export function Workspace({session,search,navigate}:{session:Session;search:string;navigate:(url:string,replace?:boolean)=>void}){
 const params=new URLSearchParams(search),view=params.get('view')||'home',id=params.get('id')||'',storeId=params.get('store')||'',newQa=params.get('new')==='1'
 const [invitationToken,setInvitationToken]=useState(pendingInvitation)
 const [month,setMonth]=useState(()=>seoulDate(new Date().toISOString()).slice(0,7))
 const route:Route=(next,id)=>{const query=new URLSearchParams({view:next});if(storeId)query.set('store',storeId);if(id)query.set('id',id);navigate(`/home?${query}`)}
 useEffect(()=>{const expired=()=>navigate('/login?error=session_expired',true);window.addEventListener('jidan-session-expired',expired);return()=>window.removeEventListener('jidan-session-expired',expired)},[navigate])
 const owner=session.accountType==='OWNER'
 const loadOwner=useMemo(()=>(signal:AbortSignal)=>call('getOwnerHome',{signal,query:storeId?{storeId}:undefined}),[storeId])
 const loadWorker=useMemo(()=>async(signal:AbortSignal)=>{const [home,calendar]=await Promise.all([call('getWorkerHome',{signal}),call('getMyWorkCalendarMonth',{signal,query:{month}})]);return {home,calendar}},[month])
 const loadProfile=useMemo(()=>(signal:AbortSignal)=>profileService.read(signal),[])
 const changeMonth=(year:number,index:number)=>setMonth(`${year}-${String(index+1).padStart(2,'0')}`)
 if(owner&&view==='add-store')return <AddStore onBack={()=>route('home')} onCreated={id=>navigate(`/home?store=${id}`)}/>
 if(view==='notifications')return <Notifications owner={owner} navigate={navigate} onBack={()=>route('home')}/>
 if(!owner){
  if(invitationToken)return <TokenInvitation token={invitationToken} onResponded={clearInvitation} onDone={()=>{clearInvitation();setInvitationToken(null);route('home')}}/>
  if(view==='qa')return <WorkerQa key={`${storeId}:${id}:${newQa}`} storeId={storeId} id={id} newChat={newQa} navigate={navigate}/>
  if(view==='manual')return <WorkerManual storeId={storeId} id={id} navigate={navigate} onBack={()=>route('home')}/>
  if(view==='invitation')return <ReceivedInvitation id={id} route={route}/>
  if(view==='work-request')return <WorkerRequest id={id} route={route}/>
  if(['jobs','job','apply','application'].includes(view))return <WorkerJobs view={view} id={id} route={route}/>
  if(view==='applications')return <WorkerApplications route={route}/>
  if(view==='profile')return <Resource load={loadProfile} onBack={()=>route('home')}>{data=><WorkerProfile initialProfile={data} service={profileService} onBack={()=>route('home')}/>}</Resource>
  return <Resource key={month} load={loadWorker} onBack={()=>route('home')}>{({home,calendar})=><WorkerHome displayName={home.name} data={workerHomeData(home,calendar)} initialDate={new Date(`${seoulDate(new Date().toISOString()).startsWith(month)?seoulDate(new Date().toISOString()):`${month}-01`}T12:00:00`)} onMonthChange={changeMonth} onSelectJob={id=>route('job',id)} onManual={()=>route('manual')} onProfile={()=>route('profile')} onJobs={()=>route('jobs')} onApplications={()=>route('applications')} onNotifications={()=>route('notifications')}/>}</Resource>
 }
 return <Resource load={loadOwner} onBack={()=>route('home')}>{home=>{
 const selected=home.stores.find(s=>s.id===home.selectedStoreId)
 const selectedRoute:Route=(next,id)=>{const query=new URLSearchParams({view:next});if(selected)query.set('store',selected.id);if(id)query.set('id',id);navigate(`/home?${query}`)}
 if(selected){
  if(['jobs','job','applicants','onboarding','create-job'].includes(view)&&selected.permissions.includes('MANAGE_JOB_POSTINGS'))return <OwnerJobs view={view} id={id} storeId={selected.id} storeName={selected.name} route={selectedRoute}/>
  if(['store','workers','worker'].includes(view)&&selected.permissions.includes('MANAGE_STORE'))return <StorePages view={view} id={id} storeId={selected.id} storeName={selected.name} route={selectedRoute}/>
  if(['invitations','invite'].includes(view)&&selected.permissions.includes('INVITE_WORKERS'))return <InvitationPages view={view} storeId={selected.id} storeName={selected.name} route={selectedRoute}/>
  if(view==='manual'&&selected.permissions.includes('MANAGE_MANUALS'))return <OwnerManual storeId={selected.id} onBack={()=>selectedRoute('home')}/>
 }
 return <><OwnerDashboard home={home} month={month} changeMonth={changeMonth} route={selectedRoute} onStoreChange={id=>navigate(`/home?store=${encodeURIComponent(id)}`)}/>{view!=='home'&&<p role="alert">이 매장에서는 해당 기능을 사용할 수 없어요.</p>}</>
 }}</Resource>
}
function OwnerDashboard({home,month,changeMonth,route,onStoreChange}:{home:Awaited<ReturnType<typeof call<'getOwnerHome'>>>;month:string;changeMonth:(y:number,m:number)=>void;route:Route;onStoreChange:(id:string)=>void}){
 const selected=home.stores.find(s=>s.id===home.selectedStoreId)
 const load=useMemo(()=>(signal:AbortSignal)=>selected?.approvalStatus==='PENDING'?Promise.resolve({month,timezone:'Asia/Seoul' as const,events:[],asOf:home.asOf}):call('getOwnerWorkCalendarMonth',{signal,query:{month,storeId:home.selectedStoreId||undefined}}),[month,home.selectedStoreId,home.asOf,selected?.approvalStatus])
 return <Resource key={month} load={load} onBack={()=>route('home')}>{calendar=><OwnerHome storePicker={home.stores.length>1?<label>관리 매장 선택 <select value={home.selectedStoreId||''} onChange={e=>onStoreChange(e.target.value)}>{home.stores.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>:undefined} displayName={home.name} data={ownerHomeData(home,calendar)} initialDate={new Date(`${seoulDate(new Date().toISOString()).startsWith(month)?seoulDate(new Date().toISOString()):`${month}-01`}T12:00:00`)} onMonthChange={changeMonth} readOnlyCalendar onAddStore={()=>route('add-store')} onManage={()=>route('store')} onCreateJob={()=>route('create-job')} onJobs={()=>route('jobs')} onSelectJob={j=>route('job',j.id)} onManual={()=>route('manual')} onNotifications={()=>route('notifications')}/>}</Resource>
}
function OwnerManual({storeId,onBack}:{storeId:string;onBack:()=>void}){
 const service=useMemo(()=>liveManualService(storeId),[storeId])
 return <ManualAuthoring service={service} onBack={onBack}/>
}
