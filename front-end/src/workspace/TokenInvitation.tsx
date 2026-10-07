import {useMemo} from 'react'
import {mutation} from '../api/operations'
import {InvitationAccept} from '../invitation/InvitationForms'
import {seoulDate} from '../home/api'
import {Resource} from './Resource'
import type {InvitationService} from '../invitation/model'
function tokenService(token:string):InvitationService{const write=mutation();return {create:async()=>{throw Error('UNSUPPORTED')},respond:async(_id,choice,signal)=>{if(choice==='accept')await write('acceptStoreInvitation',{signal,input:{token}});else await write('declineStoreInvitation',{signal,input:{token}})}}}
export function TokenInvitation({token,onDone,onResponded}:{token:string;onDone:()=>void;onResponded:()=>void}){
 const load=useMemo(()=>{const read=mutation();return (signal:AbortSignal)=>read('previewStoreInvitation',{signal,input:{token}})},[token])
 const service=useMemo(()=>tokenService(token),[token])
 return <Resource load={load} onBack={onDone}>{i=><InvitationAccept invitation={{id:i.invitationId,email:'',storeName:i.store.name,sentAt:'',expiresAt:seoulDate(i.expiresAt),accessUntil:i.accessExpiresAt?seoulDate(i.accessExpiresAt):'별도 종료 전까지',status:i.status}} service={service} onBack={onDone} onResponded={onResponded}/>}</Resource>
}
