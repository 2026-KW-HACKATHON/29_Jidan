export function pause(ms:number,signal:AbortSignal):Promise<void>{
 signal.throwIfAborted()
 return new Promise((resolve,reject)=>{const cancel=()=>{clearTimeout(timer);signal.removeEventListener('abort',cancel);reject(signal.reason)};const timer=setTimeout(()=>{signal.removeEventListener('abort',cancel);resolve()},ms);signal.addEventListener('abort',cancel,{once:true})})
}
/** Hidden tabs pause traffic. Returning to the page causes a fresh authoritative read. */
export async function waitVisible(signal:AbortSignal){
 signal.throwIfAborted()
 if(document.visibilityState!=='hidden')return
 await new Promise<void>((resolve,reject)=>{const clean=()=>{document.removeEventListener('visibilitychange',changed);signal.removeEventListener('abort',cancel)};const changed=()=>{if(document.visibilityState!=='hidden'){clean();resolve()}};const cancel=()=>{clean();reject(signal.reason)};document.addEventListener('visibilitychange',changed);signal.addEventListener('abort',cancel,{once:true})})
}
