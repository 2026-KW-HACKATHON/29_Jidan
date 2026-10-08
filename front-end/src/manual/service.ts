import { operations, schemas } from './contract.generated'
import type { Operations } from './types'
import { matches } from './validation'
import {withDeadline,DeadlineExceeded} from '../async/deadline'
export type Operation = keyof Operations
export type RequestOptions = { signal: AbortSignal; key?: string }
export type Result<T> = { data: T; retryAfterMs: number }
export type ManualService = { storeId: string; call: <N extends Operation>(name:N, params:Record<string,string>, input:Operations[N]['input'], options:RequestOptions)=>Promise<Result<Operations[N]['output']>> }
export class ManualError extends Error {
 readonly code:string
 readonly status:number
 constructor(code:string,status=0){super(code);this.name='ManualError';this.code=code;this.status=status}
}
export const newKey=()=>crypto.randomUUID()
/** #123 supplies CSRF/auth lifecycle. No cookie parsing or speculative login is owned here. */
export function createManualHttpService(storeId:string, csrf:()=>string|null, transport:typeof fetch=fetch):ManualService {
 return {storeId, async call<N extends Operation>(name:N,params:Record<string,string>,input:Operations[N]['input'],options:RequestOptions):Promise<Result<Operations[N]['output']>> {
  const op=operations[name];let path:string=op.path
  path=path.replace(/\{(\w+)\}/g,(_,key)=>{const value=key==='storeId'?storeId:params[key];if(!value || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(value))throw new ManualError('INVALID_TARGET');return encodeURIComponent(value)})
  if(name==='getManualInterviewTurns'){const query=new URLSearchParams({page:params.page??'0',size:'100'});path+='?'+query.toString()}
  if(/\{/.test(path)||/\/\//.test(path))throw new ManualError('INVALID_TARGET')
  const headers=new Headers(); const write=op.method!=='GET'
  if(write){const token=csrf();if(!token)throw new ManualError('CSRF_REQUIRED');headers.set('X-CSRF-Token',token);if(!options.key)throw new ManualError('IDEMPOTENCY_KEY_REQUIRED');headers.set('Idempotency-Key',options.key)}
  if(op.request&&op.request!=='ManualUploadInput'&&!matches(schemas[op.request],input,schemas))throw new ManualError('INVALID_REQUEST')
  if(op.request==='ManualUploadInput'&&(!(input instanceof FormData)||!['MANUAL_PHOTO','INTERVIEW_AUDIO'].includes(String(input.get('purpose')))||!(input.get('file') instanceof Blob)))throw new ManualError('INVALID_REQUEST')
  let body:BodyInit|undefined
  if(input instanceof FormData) body=input
  else if(input!==undefined){headers.set('Content-Type','application/json');body=JSON.stringify(input)}
  const controller=new AbortController(),abort=()=>controller.abort(options.signal.reason)
  options.signal.addEventListener('abort',abort,{once:true});if(options.signal.aborted)abort()
  try{return await withDeadline(async(signal)=>{
  const response=await transport(path,{method:op.method,headers,body,signal,credentials:'include',cache:'no-store'})
  signal.throwIfAborted()
  if(!response.ok){let code='REQUEST_FAILED';try {const error=await response.json();if(typeof error.code==='string')code=error.code}catch{/* Never expose provider payloads. */}throw new ManualError(code,response.status)}
  const retry=response.headers.get('Retry-After');const seconds=retry?Number(retry):NaN; const date=retry?Date.parse(retry):NaN
  const retryAfterMs=Number.isFinite(seconds)?Math.max(2000,seconds*1000):Number.isFinite(date)?Math.max(2000,date-Date.now()):2000
  let data:unknown
  if(op.response==='void')data=undefined
  else if(op.response==='Blob')data=await response.blob()
  else {data=await response.json();if(!matches(schemas[op.response],data,schemas))throw new ManualError('INVALID_RESPONSE')}
  if(data && typeof data==='object' && 'storeId' in data && data.storeId!==storeId)throw new ManualError('INVALID_RESPONSE')
  return {data:data as Operations[N]['output'],retryAfterMs}
  },controller,input instanceof FormData?60_000:10_000)}catch(error){if(error instanceof DeadlineExceeded)throw new ManualError('REQUEST_TIMEOUT');throw error}finally{options.signal.removeEventListener('abort',abort)}
 }}
}
export function errorMessage(error:unknown):string {
 const code=error instanceof ManualError?error.code:''
 const messages:Record<string,string>={REQUEST_TIMEOUT:'응답을 기다리는 시간이 길어졌어요. 같은 요청을 다시 시도할 수 있어요.',INVALID_REQUEST:'보낼 내용을 확인하고 다시 시도해 주세요.',SESSION_EXPIRED:'다시 로그인해 주세요.',CSRF_INVALID:'로그인 상태를 다시 확인해 주세요.',REVIEW_NOT_READY:'요약 처리가 끝나면 최종 검토로 갈 수 있어요.',INTERVIEW_INCOMPLETE:'남은 질문에 먼저 답해 주세요.',MANUAL_ISSUES_NOT_ACKNOWLEDGED:'부족한 내용을 확인해 주세요.',PHOTO_ALREADY_ATTACHED:'이미 이 업무에 연결된 사진이에요.',PHOTO_INVALID:'JPG·PNG·WebP 사진을 10 MiB 이하로 올려 주세요.',PHOTO_LIMIT:'한 업무에는 사진을 20장까지 연결할 수 있어요.',INVALID_RESPONSE:'응답을 확인할 수 없어요. 다시 불러와 주세요.',TRANSCRIPTION_FAILED:'음성을 알아듣지 못했어요. 다시 녹음하거나 재시도해 주세요.',RECORDING_EMPTY:'녹음된 음성이 없어요. 다시 말해 주세요.',RECORDING_LIMIT:'음성은 20 MiB, 2분까지 보낼 수 있어요.',MIC_DENIED:'마이크 권한을 허용해 주세요.',MIC_UNSUPPORTED:'이 브라우저는 음성 녹음을 지원하지 않아요.',REVISION_CONFLICT:'내용이 변경됐어요. 최신 내용을 확인해 주세요.',MANUAL_VERSION_CONFLICT:'초안이 바뀌었어요. 최신 초안을 확인해 주세요.',MANUAL_CORRECTION_IN_PROGRESS:'수정 중이에요. 완료 후 다시 시도해 주세요.',CORRECTION_CLARIFICATION_REQUIRED:'어떤 내용을 어떻게 바꿀지 다시 말해 주세요.',MANUAL_REFERENCE_CONFLICT:'연결된 업무를 확인하고 다시 말해 주세요.',STORE_APPROVAL_REQUIRED:'매장 승인 후 사용할 수 있어요.',UNAUTHENTICATED:'다시 로그인해 주세요.',CSRF_REQUIRED:'로그인 상태를 다시 확인해 주세요.'}
 return messages[code]??'처리하지 못했어요. 다시 시도해 주세요.'
}
