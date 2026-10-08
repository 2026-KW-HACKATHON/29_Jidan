import {ApiError, apiUrl, type RequestOptions} from '../api/client'
import {withDeadline, DeadlineExceeded} from '../async/deadline'
import {matches, type Schema} from '../manual/validation'
import {operations, schemas} from './contract.generated'
import type {Operations} from './types.generated'
export type Operation = keyof Operations
export type Options = {signal: AbortSignal; params?: Record<string,string>; query?: RequestOptions['query']; key?: string}
export type Result<T> = {data: T; retryAfterMs: number}
export type QaService = {storeId: string; call: <N extends Operation>(name:N, input:Operations[N]['input'], options:Options)=>Promise<Result<Operations[N]['output']>>}
const uuid = (value:unknown) => matches({type:'string',format:'uuid'}, value, schemas)
export function validatePhoto(file:Blob) {
 if (!['image/jpeg','image/png','image/webp'].includes(file.type) || !file.size || file.size>10*1024*1024) throw new ApiError(0,'QA_PHOTO_INVALID')
}
export function retryDelay(value:string|null) {
 const seconds = value?.trim() ? Number(value) : NaN
 const delay = Number.isFinite(seconds) ? seconds*1000 : value ? Date.parse(value)-Date.now() : 2000
 return Number.isFinite(delay) ? Math.min(60_000,Math.max(2000,delay)) : 2000
}
/** Store-scoped transport. The caller owns each write's stable intent key. */
export function createQaService(storeId:string, transport:typeof fetch=fetch):QaService {
 return {storeId, async call<N extends Operation>(name:N,input:Operations[N]['input'],options:Options):Promise<Result<Operations[N]['output']>> {
  const op=operations[name], params:Record<string,string>={...options.params,storeId}
  const path=op.path.replace(/\{(\w+)\}/g,(_,key)=>{const value=params[key];if(!uuid(value))throw new ApiError(0,'INVALID_REQUEST');return encodeURIComponent(value)})
  for(const p of op.parameters){const value=p.in==='path'?params[p.name]:options.query?.[p.name];if(value===undefined?p.required:!matches(p.schema as Schema,value,schemas))throw new ApiError(0,'INVALID_REQUEST')}
  const write=op.method!=='GET'
  if(write&&!options.key)throw new ApiError(0,'INVALID_REQUEST')
  if(op.multipart){
   if(!(input instanceof FormData)||!(input.get('file') instanceof Blob))throw new ApiError(0,'INVALID_REQUEST')
   const file=input.get('file') as Blob,purpose=input.get('purpose')
   if(purpose==='QUESTION_IMAGE')validatePhoto(file)
   else if(purpose!=='QUESTION_AUDIO'||!['audio/webm','audio/mp4','audio/mpeg','audio/wav'].includes(file.type)||!file.size||file.size>20*1024*1024)throw new ApiError(0,'INVALID_REQUEST')
  }else if(op.method!=='GET'&&input!==undefined&&!matches(op.input as Schema,input,schemas))throw new ApiError(0,'INVALID_REQUEST')
  const controller=new AbortController(),abort=()=>controller.abort(options.signal.reason)
  if(options.signal.aborted)abort();else options.signal.addEventListener('abort',abort,{once:true})
  try{return await withDeadline(async signal=>{
   signal.throwIfAborted()
   const headers=new Headers({Accept:op.blob?'image/*, audio/*':'application/json'})
   async function checked(response:Response){
    if(response.status===401)window.dispatchEvent(new Event('jidan-session-expired'))
    if(!response.ok){const body=await response.json().catch(()=>null);throw new ApiError(response.status,typeof body?.code==='string'?body.code:'UNKNOWN_ERROR')}
    return response
   }
   if(write){
    const response=await checked(await transport('/api/auth/csrf',{signal,credentials:'include',cache:'no-store',redirect:'error'})),data=await response.json()
    if(typeof data?.csrfToken!=='string'||!data.csrfToken)throw new ApiError(0,'INVALID_RESPONSE')
    headers.set('X-CSRF-Token',data.csrfToken);headers.set('Idempotency-Key',options.key!)
   }
   signal.throwIfAborted()
   let body:BodyInit|undefined
   if(input instanceof FormData)body=input
   else if(input!==undefined){headers.set('Content-Type','application/json');body=JSON.stringify(input)}
   const response=await checked(await transport(apiUrl(path,options.query),{method:op.method,headers,body,signal,credentials:'include',cache:'no-store',redirect:'error'}))
   const data:unknown=response.status===204?undefined:op.blob?await response.blob():await response.json()
   signal.throwIfAborted()
   if(op.output&&!matches(op.output as Schema,data,schemas))throw new ApiError(0,'INVALID_RESPONSE')
   if(data&&typeof data==='object'&&!(data instanceof Blob)){
    const row=data as Record<string,unknown>,target=options.params?.questionId??options.params?.transcriptionId
    if('storeId'in row&&row.storeId!==storeId||'conversationId'in row&&row.conversationId!==options.params?.conversationId||target&&row.id!==target)throw new ApiError(0,'INVALID_RESPONSE')
    if(name==='getQAConversation'){
     const detail=data as Operations['getQAConversation']['output']
     if(detail.conversation.id!==options.params?.conversationId||detail.conversation.storeId!==storeId||detail.turns.some(t=>t.conversationId!==detail.conversation.id))throw new ApiError(0,'INVALID_RESPONSE')
    }
    if(name==='listMyQAConversations'&&(data as Operations['listMyQAConversations']['output']).items.some(c=>c.storeId!==storeId))throw new ApiError(0,'INVALID_RESPONSE')
   }
   return {data:data as Operations[N]['output'],retryAfterMs:retryDelay(response.headers.get('Retry-After'))}
  },controller,op.multipart?60_000:10_000)}catch(error){
   if(options.signal.aborted)throw options.signal.reason
   if(error instanceof DeadlineExceeded)throw new ApiError(0,'CLIENT_WAIT_EXCEEDED')
   if(error instanceof ApiError)throw error
   throw new ApiError(0,'NETWORK_ERROR')
  }finally{options.signal.removeEventListener('abort',abort)}
 }}
}
