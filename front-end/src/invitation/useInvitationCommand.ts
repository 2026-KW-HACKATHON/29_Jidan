import { useEffect,useRef,useState } from 'react'
import { DeadlineExceeded,withDeadline } from '../async/deadline'
/** Cancellation and duplicate protection are shared by invitation commands. */
export function useInvitationCommand() {
 const [busy,setBusy]=useState(false),[failed,setFailed]=useState(false)
 const request=useRef<AbortController|null>(null),lock=useRef(false)
 useEffect(()=>()=>request.current?.abort(),[])
 function cancel(){request.current?.abort();lock.current=false;setBusy(false);setFailed(false)}
 async function run<T>(operation:(signal:AbortSignal)=>Promise<T>,onSuccess:(result:T)=>void):Promise<boolean> {
  if(lock.current)return false
  lock.current=true;setBusy(true);setFailed(false)
  const controller=new AbortController();request.current=controller
  try {const result=await withDeadline(operation,controller);if(controller.signal.aborted)return false;onSuccess(result);return true}
  catch(error){if(!controller.signal.aborted||error instanceof DeadlineExceeded)setFailed(true);return false}
  finally {if(request.current===controller && (!controller.signal.aborted||controller.signal.reason instanceof DeadlineExceeded)){lock.current=false;setBusy(false)}}
 }
 return {busy,failed,run,cancel}
}
