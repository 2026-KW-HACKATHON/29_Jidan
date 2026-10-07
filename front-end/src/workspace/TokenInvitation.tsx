import {useMemo} from 'react'
import {mutation} from '../api/operations'
import {InvitationAccept} from '../invitation/InvitationForms'
import {seoulDate} from '../home/api'
import {Resource} from './Resource'
export function TokenInvitation({token,onDone,onResponded}:{token:string;onDone:()=>void;onResponded:()=>void}){
 const write=useMemo(()=>mutation(),[]),load=useMemo(()=>(signal:AbortSignal)=>write('previewStoreInvitation',{signal,input:{token}}),[write,token])
 const service=useMemo(()=>({create:async()=>{throw Error('UNSUPPORTED')},respond:async(_id:string,choice:'accept'|'decline',signal:AbortSignal)=>{await write(choice==='accept'?'acceptStoreInvitation':'declineStoreInvitation',{signal,input:{token}})}}),[write,token])
 return <Resource load={load} onBack={onDone}>{i=><InvitationAccept invitation={{id:i.invitationId,email:'',storeName:i.store.name,sentAt:'',expiresAt:seoulDate(i.expiresAt),accessUntil:i.accessExpiresAt?seoulDate(i.accessExpiresAt):'별도 종료 전까지',status:i.status}} service={service} onBack={onDone} onResponded={onResponded}/>}</Resource>
}
