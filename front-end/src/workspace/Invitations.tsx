import {useMemo,useState} from 'react'
import {call,pages} from '../api/operations'
import {Resource} from './Resource'
import type {Route} from './Jobs'
import {invitationApi,invitationFromApi} from '../invitation/api'
import {InvitationCreate} from '../invitation/InvitationForms'
import {InvitationManagement} from '../invitation/InvitationManagement'
import type {Invitation} from '../invitation/model'
export function InvitationPages({view,storeId,storeName,route}:{view:string;storeId:string;storeName:string;route:Route}){
 const service=useMemo(()=>invitationApi(storeId,storeName),[storeId,storeName]),load=useMemo(()=>(signal:AbortSignal)=>pages(page=>call('listStoreInvitations',{signal,params:{storeId},query:{page,size:100}}),signal),[storeId])
 if(view==='invite')return <InvitationCreate storeName={storeName} service={service} onBack={()=>route('invitations')} onCreated={()=>route('invitations')}/>
 return <Resource load={load} onBack={()=>route('store')}>{items=><InvitationList initial={items.map(i=>invitationFromApi(i,storeName))} storeId={storeId} storeName={storeName} route={route}/>}</Resource>
}
function InvitationList({initial,storeId,storeName,route}:{initial:Invitation[];storeId:string;storeName:string;route:Route}){const [items,setItems]=useState(initial);const service=useMemo(()=>invitationApi(storeId,storeName,updated=>setItems(values=>values.map(i=>i.id===updated.id?updated:i))),[storeId,storeName]);return <InvitationManagement storeName={storeName} invitations={items} service={service} onBack={()=>route('store')} onCreate={()=>route('invite')} onCancelled={id=>setItems(values=>values.map(i=>i.id===id?{...i,status:'CANCELLED'}:i))}/>}
