import {useEffect,useRef,useState} from 'react'
import {errorMessage,newKey,ManualError} from './service'
/** A retry replays the exact captured request, never rebases it onto newer state. */
export function useManualTask(){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[canRetry,setCanRetry]=useState(false);const request=useRef<AbortController|null>(null),locked=useRef(false),last=useRef<((signal:AbortSignal,key:string)=>Promise<void>)|null>(null),key=useRef('')
 useEffect(()=>()=>{request.current?.abort()},[])
 async function execute(operation:(signal:AbortSignal,key:string)=>Promise<void>,retry=false){if(locked.current)return;locked.current=true;if(!retry){last.current=operation;key.current=newKey()}const controller=new AbortController();request.current=controller;setBusy(true);setError('')
  try{await operation(controller.signal,key.current);if(!controller.signal.aborted)setCanRetry(false)}catch(e){if(!controller.signal.aborted){setError(errorMessage(e));if(e instanceof ManualError&&['REVISION_CONFLICT','MANUAL_VERSION_CONFLICT','MANUAL_STATE_CONFLICT'].includes(e.code))last.current=null;setCanRetry(last.current!==null)}}
  finally{if(!controller.signal.aborted){locked.current=false;setBusy(false)}}
 }
 return {busy,error,canRetry:!!error&&canRetry,run:(operation:(signal:AbortSignal,key:string)=>Promise<void>)=>execute(operation),retry:()=>{if(last.current)void execute(last.current,true)},clearError:()=>setError('')}
}
