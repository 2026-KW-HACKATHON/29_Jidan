import {apiRequest,ApiError,type RequestOptions} from './client'
import {operations,schemas} from './contract.generated'
import type {Operations} from './types.generated'
import {matches,type Schema} from '../manual/validation'
export type Operation = keyof Operations
export async function call<N extends Operation>(name:N, options:{signal:AbortSignal;params?:Record<string,string>;query?:RequestOptions['query'];input?:Operations[N]['input'];key?:string}):Promise<Operations[N]['output']>{
 const op=operations[name]
 const path=op.path.replace(/\{(\w+)\}/g,(_,key)=>{const value=options.params?.[key];if(!value||!matches({type:'string',format:'uuid'},value,schemas))throw new ApiError(0,'INVALID_REQUEST');return value})
 for(const p of op.parameters){const value=p.in==='path'?options.params?.[p.name]:options.query?.[p.name];if(value===undefined){if(p.required)throw new ApiError(0,'INVALID_REQUEST')}else if(!matches(p.schema as Schema,value,schemas))throw new ApiError(0,'INVALID_REQUEST')}
 if(options.input!==undefined&&!matches(op.input as Schema,options.input,schemas))throw new ApiError(0,'VALIDATION_ERROR')
 if(op.method!=='GET'&&!options.key)throw new ApiError(0,'INVALID_REQUEST')
 try {
  const data=await apiRequest<Operations[N]['output']>(path,{signal:options.signal,method:op.method,query:options.query,body:options.input,idempotencyKey:options.key})
  if(op.output&&!matches(op.output as Schema,data,schemas))throw new ApiError(0,'INVALID_RESPONSE')
  return data
 }catch(error){if(error instanceof ApiError&&error.status===401)window.dispatchEvent(new Event('jidan-session-expired'));throw error}
}
/** Keep the exact key on response loss; a changed operation/target/body is a new intent. */
export function mutation(){let previous='',key='';return async<N extends Operation>(name:N,options:Omit<Parameters<typeof call<N>>[1],'key'>)=>{const stamp=JSON.stringify([name,options.params,options.input]);if(stamp!==previous){previous=stamp;key=crypto.randomUUID()}const result=await call(name,{...options,key});previous='';return result}}
export async function pages<T>(read:(page:number)=>Promise<{items:T[];page:number;totalItems:number}>,signal:AbortSignal):Promise<T[]>{
 const result:T[]=[]
 for(let page=0;page<100;page++){signal.throwIfAborted();const value=await read(page);signal.throwIfAborted();if(value.page!==page)throw new ApiError(0,'INVALID_RESPONSE');result.push(...value.items);if(!value.items.length||result.length>=value.totalItems)return result}
 throw new ApiError(0,'TOO_MANY_RESULTS')
}
