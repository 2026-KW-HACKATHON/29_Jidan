import {useCallback,useEffect,useMemo,useState} from 'react'
import {ApiError,errorMessage} from '../api/client'
import {ManualAuthoring} from '../manual/ManualAuthoring'
import {liveManualService} from '../manual/live'
import {QaChat} from '../qa/QaChat'
import {createQaService} from '../qa/service'
import {Button} from '../ui/Button'
import {LoadingState} from '../ui/LoadingState'
import {Modal} from '../ui/Modal'
import {DemoManual} from './DemoManual'
import {demoRole,demoRoute,demoStart,type DemoRole} from './routes'
import {startDemoSession,resetDemoSession,type DemoSession} from './session'
import './Demo.css'

export default function DemoApp({role=demoRole()}:{role?:DemoRole|null}={}){
 if(!role)return <div className="demo-unavailable"><h1>데모 주소를 확인해 주세요</h1><p>일반 사용자 또는 점주 데모 주소에서 체험할 수 있어요.</p></div>
 return <DemoExperience key={role} role={role}/>
}
function DemoExperience({role}:{role:DemoRole}){
 const [route,setRoute]=useState(()=>demoRoute(window.location.href,role))
 const [boot,setBoot]=useState<{session:DemoSession;attempt:number}|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0)
 const session=boot?.attempt===attempt?boot.session:null
 const [confirm,setConfirm]=useState(false),[resetting,setResetting]=useState(false),[expired,setExpired]=useState(false)
 const navigate=useCallback((url:string,replace=false)=>{
  const next=demoRoute(url,role)
  window.history[replace?'replaceState':'pushState'](null,'',next.url);setRoute(next)
 },[role])
 useEffect(()=>{
  const changed=()=>navigate(window.location.href,true)
  const restored=(event:PageTransitionEvent)=>{if(event.persisted){setError('');setAttempt(value=>value+1)}}
  const ended=()=>setExpired(true)
  window.addEventListener('popstate',changed);window.addEventListener('pageshow',restored);window.addEventListener('jidan-session-expired',ended)
  return()=>{window.removeEventListener('popstate',changed);window.removeEventListener('pageshow',restored);window.removeEventListener('jidan-session-expired',ended)}
 },[navigate])
 useEffect(()=>{
  let current=true
  void startDemoSession(role).then(value=>{if(current){setBoot({session:value,attempt});setExpired(false);navigate(value.fresh?demoStart(role):window.location.href,true)}})
   .catch(failure=>{if(current)setError(errorMessage(failure instanceof ApiError?failure.code:'NETWORK_ERROR'))})
  return()=>{current=false}
 },[role,attempt,navigate])
 async function reset(){
  if(resetting)return
  setResetting(true);setError('')
  try{const value=await resetDemoSession(role,new AbortController().signal);setBoot({session:value,attempt});setConfirm(false);setExpired(false);navigate(demoStart(role),true)}
  catch(failure){setError(errorMessage(failure instanceof ApiError?failure.code:'NETWORK_ERROR'));if(failure instanceof ApiError&&failure.status===401)setExpired(true)}
  finally{setResetting(false)}
 }
 return <div className="demo-shell"><header className="demo-toolbar">
  <button className="demo-brand" onClick={()=>navigate(demoStart(role))} aria-label="지단 데모 처음으로">지단 <span>{role==='OWNER'?'점주 체험':'매뉴얼 체험'}</span></button>
  <button className="demo-reset" disabled={!session||resetting||expired} onClick={()=>{setError('');setConfirm(true)}}>다시 체험</button>
 </header><div className="demo-content" inert={resetting||expired}>
  {!session?<div className="demo-loading">{error?<><p role="alert">{error}</p><Button onClick={()=>{setError('');setAttempt(value=>value+1)}}>다시 연결하기</Button></>:<LoadingState message="체험을 준비하고 있어요."/>}</div>:<DemoScreen key={session.csrfToken} session={session} route={route} navigate={navigate}/>}
 </div>
 <Modal open={confirm&&!expired} title="처음부터 다시 체험할까요?" description="지금 작성한 매뉴얼과 질문은 새 체험에서 보이지 않아요." onClose={()=>{if(!resetting){setConfirm(false);setError('')}}} confirmLabel="새 체험 시작" showCancel closeOnConfirm={false} onConfirm={()=>void reset()} busy={resetting} summary={error?<p role="alert">{error}</p>:undefined}/>
 <Modal open={expired} title="체험 시간이 끝났어요" description="새 체험을 시작하면 다시 사용할 수 있어요." onClose={()=>{}} confirmLabel="새 체험 시작" closeOnConfirm={false} busy={!session&&!error} summary={error?<p role="alert">{error}</p>:undefined} onConfirm={()=>{setConfirm(false);setError('');setAttempt(value=>value+1)}}/>
 </div>
}
function DemoScreen({session,route,navigate}:{session:DemoSession;route:ReturnType<typeof demoRoute>;navigate:(url:string,replace?:boolean)=>void}){
 const manual=useMemo(()=>liveManualService(session.storeId),[session.storeId]),qa=useMemo(()=>createQaService(session.storeId),[session.storeId])
 const created=useCallback((id:string)=>navigate(`/questions?id=${id}`,true),[navigate])
 if(route.path==='/author')return <ManualAuthoring service={manual} onBack={()=>navigate('/manual')}/>
 if(route.path==='/questions')return <QaChat key={route.id} service={qa} storeName="지단 카페 체험 매장" conversationId={route.id} onCreated={created} onBack={()=>navigate('/manual')} onLatest={()=>navigate('/manual')}/>
 return <DemoManual storeId={session.storeId} id={route.id} role={session.role} navigate={navigate}/>
}
