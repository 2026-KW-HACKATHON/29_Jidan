import {apiRequest,ApiError} from '../api/client'
import {isDemoId,type DemoRole} from './routes'
export type DemoSession={role:DemoRole;storeId:string;expiresAt:string;csrfToken:string;fresh:boolean}
let pending:Promise<DemoSession>|null=null
function validate(value:unknown,role:DemoRole):Omit<DemoSession,'fresh'> {
  const data=value as Partial<DemoSession>|null
  if(!data||data.role!==role||!isDemoId(data.storeId)||
     typeof data.expiresAt!=='string'||!Number.isFinite(Date.parse(data.expiresAt))||Date.parse(data.expiresAt)<=Date.now()||
     typeof data.csrfToken!=='string'||!data.csrfToken)throw new ApiError(0,'INVALID_RESPONSE')
  return data as Omit<DemoSession,'fresh'>
}
export function startDemoSession(role:DemoRole):Promise<DemoSession> {
  // StrictMode's remounts share the anonymous POST, so only one visitor is allocated.
  if(pending)return pending
  const signal=new AbortController().signal
  pending=(async()=>{
    let previousExpiry:string|null=null
    try{previousExpiry=(await apiRequest<{expiresAt:string}>('/auth/csrf',{signal})).expiresAt}
    catch(error){if(!(error instanceof ApiError&&error.status===401))throw error}
    const session=validate(await apiRequest('/demo/session',{signal,method:'POST',body:{},allowExpiredCsrf:true}),role)
    return {...session,fresh:session.expiresAt!==previousExpiry}
  })().finally(()=>{pending=null})
  return pending
}
export async function resetDemoSession(role:DemoRole,signal:AbortSignal):Promise<DemoSession> {
  return {...validate(await apiRequest('/demo/reset',{signal,method:'POST',body:{}}),role),fresh:true}
}
