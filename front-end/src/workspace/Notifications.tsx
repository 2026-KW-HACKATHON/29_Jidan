import {notificationRoute} from './notificationRoute'
import {useMemo} from 'react'
import {call,mutation,pages} from '../api/operations'
import type {Notification} from '../api/types.generated'
import {useCommand} from '../async/useCommand'
import {Resource} from './Resource'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
export function Notifications({owner,navigate,onBack}:{owner:boolean;navigate:(url:string)=>void;onBack:()=>void}){
 const load=useMemo(()=>(signal:AbortSignal)=>pages(page=>call('listMyNotifications',{signal,query:{page,size:100}}),signal),[])
 return <Resource load={load} onBack={onBack}>{items=><NotificationList items={items} owner={owner} navigate={navigate} onBack={onBack}/>}</Resource>
}

function NotificationList({items,owner,navigate,onBack}:{items:Notification[];owner:boolean;navigate:(url:string)=>void;onBack:()=>void}){
 const {busy,failed,run}=useCommand(),write=useMemo(()=>mutation(),[])
 async function open(n:Notification){await run(signal=>write('markNotificationRead',{signal,params:{notificationId:n.id},input:{}}),()=>navigate(notificationRoute(n,owner)))}
 return <MobileLayout header={<AppBar title="알림" onBack={onBack}/>}><div className="home-content">{!items.length&&<p>도착한 알림이 없어요.</p>}{items.map(n=><Button key={n.id} intent="secondary" disabled={busy} onClick={()=>void open(n)}>{n.readAt?'':'● '}{n.title} — {n.body}</Button>)}{failed&&<p role="alert">알림을 열지 못했어요. 다시 시도해 주세요.</p>}</div></MobileLayout>
}
