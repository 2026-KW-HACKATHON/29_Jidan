import {mutation} from '../api/operations'
import type {StoreInvitation} from '../api/types.generated'
import type {Invitation,InvitationService} from './model'
import type {InvitationManagementService} from './InvitationManagement'
import {seoulDate} from '../home/api'
export function invitationFromApi(i:StoreInvitation,storeName:string):Invitation{return {id:i.id,email:i.email,storeName,sentAt:seoulDate(i.lastSentAt),expiresAt:seoulDate(i.expiresAt),accessUntil:i.accessExpiresAt?seoulDate(i.accessExpiresAt):'별도 종료 전까지',status:i.status,respondedAt:i.acceptedAt?seoulDate(i.acceptedAt):i.declinedAt?seoulDate(i.declinedAt):undefined}}
export function invitationApi(storeId:string,storeName:string):InvitationService&InvitationManagementService{const write=mutation();return {
 create:async(email,signal)=>invitationFromApi((await write('createStoreInvitation',{signal,params:{storeId},input:{email}})).invitation,storeName),
 respond:async(invitationId,choice,signal)=>{await write('respondToReceivedStoreInvitation',{signal,params:{invitationId},input:{decision:choice==='accept'?'ACCEPT':'DECLINE'}})},
 cancel:async(invitationId,signal)=>{await write('cancelStoreInvitation',{signal,params:{storeId,invitationId}})},
 resend:async(invitationId,signal)=>{await write('resendStoreInvitation',{signal,params:{storeId,invitationId}})},
}}
