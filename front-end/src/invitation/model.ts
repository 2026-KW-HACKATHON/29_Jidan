export type Invitation={id:string;email:string;storeName:string;sentAt:string;expiresAt:string;accessUntil:string;status:'pending'|'accepted'|'declined'|'expired'|'cancelled';workerName?:string;respondedAt?:string}
/** UI commands only. No endpoint or email provider is assumed. */
export type InvitationService={create:(email:string,signal:AbortSignal)=>Promise<Invitation>;respond:(id:string,choice:'accept'|'decline',signal:AbortSignal)=>Promise<void>}
export const unavailableInvitations:InvitationService={create:async()=>{throw Error('INVITATIONS_NOT_CONFIGURED')},respond:async()=>{throw Error('INVITATIONS_NOT_CONFIGURED')}}
export function validInvitationEmail(value:string) {const email=value.trim();return email.length<=254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(email)}
