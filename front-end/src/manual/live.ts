import {apiRequest,ApiError} from '../api/client'
import {createManualHttpService,type ManualService} from './service'
import {operations} from './contract.generated'
/** Keep each request's CSRF value isolated from concurrent commands. */
export function liveManualService(storeId:string):ManualService{return {storeId,async call(name,params,input,options){try{const token=operations[name].method==='GET'?null:(await apiRequest<{csrfToken:string}>('/auth/csrf',{signal:options.signal})).csrfToken;const transport:typeof fetch=async(...args)=>{const response=await fetch(...args);if(response.status===401)window.dispatchEvent(new Event('jidan-session-expired'));return response};return await createManualHttpService(storeId,()=>token,transport).call(name,params,input,options)}catch(error){if(error instanceof ApiError&&error.status===401)window.dispatchEvent(new Event('jidan-session-expired'));throw error}}}}
