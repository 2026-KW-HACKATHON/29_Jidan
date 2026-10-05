import { useMemo,useState } from 'react'
import { InvitationCreate,InvitationAccept } from '../invitation/InvitationForms'
import { InvitationManagement,type InvitationManagementService } from '../invitation/InvitationManagement'
import type { Invitation,InvitationService } from '../invitation/model'
import { navigatePreview,usePreviewLocation } from './navigation'
const sample:Invitation={id:'preview-invitation-minji',email:'minji@example.com',storeName:'명랑핫도그 광운대점',sentAt:'9월 25일',expiresAt:'10월 2일까지',accessUntil:'2026. 09. 26까지',status:'PENDING'}
const history:Invitation[]=[{...sample,id:'jisu',email:'jisu@example.com',status:'ACCEPTED',respondedAt:'9월 24일',workerName:'김지수'},{...sample,id:'yujin',email:'yujin@example.com',status:'EXPIRED',sentAt:'9월 17일',expiresAt:'9월 24일'}]
export default function InvitationsPreview() {
 const current=usePreviewLocation(),params=new URLSearchParams(current.split('?')[1]),view=params.get('view'),fail=params.has('fail')
 const [invitations,setInvitations]=useState(()=>params.has('empty')?history:[sample,...history]),[selected,setSelected]=useState(sample.id)
 const service=useMemo<InvitationService & InvitationManagementService>(()=>{let attempts=0;const check=(signal:AbortSignal)=>{signal.throwIfAborted();if(fail && attempts++===0)throw Error('MOCK_INVITATION_FAILURE')};return {create:async(email,signal)=>{check(signal);return {...sample,id:crypto.randomUUID(),email}},respond:async(_id,_choice,signal)=>check(signal),cancel:async(_id,signal)=>check(signal),resend:async(_id,signal)=>check(signal)}},[fail])
 const back=()=>navigatePreview('/__store/manage')
 const invitation=invitations.find(item=>item.id===selected)??sample
 if(view==='accept')return <InvitationAccept invitation={invitation} service={service} onBack={back} onResponded={choice=>setInvitations(items=>items.map(item=>item.id===selected?{...item,status:choice==='accept'?'ACCEPTED':'DECLINED'}:item))}/>
 if(view==='create')return <InvitationCreate storeName={sample.storeName} service={service} onBack={()=>navigatePreview('/__invitations')} onCreated={item=>{setInvitations(items=>[item,...items]);setSelected(item.id);navigatePreview('/__invitations')}}/>
 return <InvitationManagement key={view} storeName={sample.storeName} invitations={invitations} service={service} initialTab={view==='past'?1:0} initialOverlay={params.get('overlay')==='cancel'?'cancel':params.get('overlay')==='resent'?'resent':undefined} onBack={back} onCreate={()=>navigatePreview('/__invitations?view=create')} onCancelled={id=>setInvitations(items=>items.map(item=>item.id===id?{...item,status:'CANCELLED'}:item))}/>
}
