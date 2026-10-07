import {notificationRoute} from './notificationRoute'
import {notificationTime} from './notificationTime'
import {useEffect,useMemo,useState} from 'react'
import {call,mutation,pages} from '../api/operations'
import type {Notification} from '../api/types.generated'
import {useCommand} from '../async/useCommand'
import {Resource} from './Resource'
import {NotificationPreviewScreen} from '../jobs/NotificationPreviewScreen'
export function Notifications({owner,navigate,onBack}:{owner:boolean;navigate:(url:string)=>void;onBack:()=>void}){
 const load=useMemo(()=>(signal:AbortSignal)=>pages(page=>call('listMyNotifications',{signal,query:{page,size:100}}),signal),[])
 return <Resource load={load} onBack={onBack}>{items=><NotificationList items={items} owner={owner} navigate={navigate} onBack={onBack}/>}</Resource>
}
function NotificationList({items,owner,navigate,onBack}:{items:Notification[];owner:boolean;navigate:(url:string)=>void;onBack:()=>void}){
 const [now,setNow]=useState(Date.now)
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),60000);return()=>clearInterval(timer)},[])
 const [filter,setFilter]=useState<'ALL'|'UNREAD'>('ALL')
 const {busy,failed,run}=useCommand(),write=useMemo(()=>mutation(),[])
 async function open(id:string|number){
  const n=items.find(n=>n.id===id);if(!n)return
  if(n.readAt){navigate(notificationRoute(n,owner));return}
  await run(signal=>write('markNotificationRead',{signal,params:{notificationId:n.id},input:{}}),()=>navigate(notificationRoute(n,owner)))
 }
 const state=filter==='UNREAD'?'UNREAD':items.length&&items.every(n=>n.readAt)?'READ_ALL':'ALL'
 return <NotificationPreviewScreen items={items.map(n=>({id:n.id,title:n.title,desc:n.body,time:notificationTime(n.createdAt,now),isUnread:n.readAt===null}))} state={state} onFilter={setFilter} onBack={onBack} onOpen={open} busy={busy} error={failed?'알림을 열지 못했어요. 연결 상태를 확인하고 다시 눌러 주세요.':undefined}/>
}
