import { useMemo,useState } from 'react'
import { InvitationCreate,InvitationAccept } from '../invitation/InvitationForms'
import type { Invitation,InvitationService } from '../invitation/model'
import { navigatePreview,usePreviewLocation } from './navigation'
const sample:Invitation={id:'preview-invitation-minji',email:'minji@example.com',storeName:'명랑핫도그 광운대점',sentAt:'9월 25일',expiresAt:'10월 2일까지',accessUntil:'2026. 09. 26까지',status:'pending'}
export default function InvitationsPreview() {
 const current=usePreviewLocation(),params=new URLSearchParams(current.split('?')[1]),view=params.get('view'),fail=params.has('fail')
 const [invitation,setInvitation]=useState(sample)
 const service=useMemo<InvitationService>(()=>{let attempts=0;const check=(signal:AbortSignal)=>{signal.throwIfAborted();if(fail && attempts++===0)throw Error('MOCK_INVITATION_FAILURE')};return {create:async(email,signal)=>{check(signal);return {...sample,id:crypto.randomUUID(),email}},respond:async(_id,_choice,signal)=>check(signal)}},[fail])
 const back=()=>navigatePreview('/__store/manage')
 if(view==='accept')return <InvitationAccept invitation={invitation} service={service} onBack={back} onResponded={choice=>setInvitation(item=>({...item,status:choice==='accept'?'accepted':'declined'}))}/>
 return <InvitationCreate storeName={sample.storeName} service={service} onBack={back} onCreated={item=>{setInvitation(item);navigatePreview('/__invitations?view=accept')}}/>
}
