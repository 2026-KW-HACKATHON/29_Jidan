import {useCallback,useEffect,useRef,useState} from 'react'
import {ApiError} from '../api/client'
import {pause,waitVisible} from '../manual/poll'
import {accessLost,qaError} from './errors'
import type {QaService} from './service'
import type {QAQuestion,QAQuestionInput} from './types.generated'
type Action=(signal:AbortSignal)=>Promise<void>
export function mergeTurns(previous:QAQuestion[],incoming:QAQuestion[]){return [...new Map([...previous,...incoming].map(t=>[t.id,t])).values()].sort((a,b)=>a.sequence-b.sequence)}
/** Mount per store/conversation. No transcript or attachment is persisted in browser storage. */
export function useConversation(service:QaService,initialId:string) {
 const [id,setId]=useState(initialId),[turns,setTurns]=useState<QAQuestion[]>([]),[cursor,setCursor]=useState<number|null>(null)
 const [busy,setBusy]=useState(false),[loading,setLoading]=useState(!!initialId),[blocked,setBlocked]=useState(false)
 const [error,setError]=useState(''),[pending,setPending]=useState(false),[sent,setSent]=useState(0),[pollEpoch,setPollEpoch]=useState(0)
 const life=useRef<AbortController|null>(null),lock=useRef(false),retry=useRef<Action|null>(null),conversation=useRef(initialId)
 const report=useCallback((e:unknown)=>{setError(qaError(e));if(accessLost(e)){setBlocked(true);setTurns([]);retry.current=null;setPending(false)}},[])
 const run=useCallback(async(action:Action)=>{
  const controller=life.current;if(!controller||controller.signal.aborted||lock.current)return
  lock.current=true;setBusy(true);setError('');retry.current=action
  try{await action(controller.signal);controller.signal.throwIfAborted();retry.current=null;setError('')}
  catch(e){if(!controller.signal.aborted)report(e)}
  finally{if(!controller.signal.aborted){lock.current=false;setBusy(false);setLoading(false)}}
 },[report])
 const reload=useCallback(()=>run(async signal=>{
  if(!conversation.current)return
  const {data}=await service.call('getQAConversation',undefined,{signal,params:{conversationId:conversation.current},query:{size:20}})
  signal.throwIfAborted();setTurns(data.turns);setCursor(data.nextBeforeSequence);setPollEpoch(v=>v+1)
 }),[service,run])
 useEffect(()=>{
  const controller=new AbortController();life.current=controller;lock.current=false
  if(initialId)void reload()
  return()=>{controller.abort();life.current=null}
 },[initialId,reload])
 const running=turns.find(t=>t.status==='RUNNING')?.id
 useEffect(()=>{
  if(!running||blocked)return
  const controller=new AbortController()
  void (async()=>{
   while(true){
    await waitVisible(controller.signal)
    const result=await service.call('getManualQuestionResult',undefined,{signal:controller.signal,params:{conversationId:id,questionId:running}})
    controller.signal.throwIfAborted();setTurns(t=>mergeTurns(t,[result.data]))
    if(result.data.status!=='RUNNING')return
    await pause(result.retryAfterMs,controller.signal)
   }
  })().catch(e=>{if(!controller.signal.aborted){report(e);if(!accessLost(e))retry.current=async()=>{setPollEpoch(v=>v+1)}}})
  return()=>controller.abort()
 },[service,id,running,blocked,pollEpoch,report])
 function send(input:QAQuestionInput){
  if(blocked||pending||running||lock.current)return
  const createKey=crypto.randomUUID(),askKey=crypto.randomUUID()
  setPending(true)
  void run(async signal=>{
   try{
   if(!conversation.current){const {data}=await service.call('createQAConversation',{}, {signal,key:createKey});signal.throwIfAborted();conversation.current=data.id;setId(data.id)}
    const {data}=await service.call('askManualQuestion',input,{signal,key:askKey,params:{conversationId:conversation.current}})
    signal.throwIfAborted();setTurns(t=>mergeTurns(t,[data]));setPending(false);setSent(v=>v+1)
   }catch(e){
    // A definite rejection is editable; response loss must be recovered with the same intent.
    if(e instanceof ApiError&&e.status>=400&&e.status<500&&!['STATE_CONFLICT','RATE_LIMITED'].includes(e.code)){setPending(false);retry.current=null}
    throw e
   }
  })
 }
 function retryQuestion(questionId:string){
  if(blocked||pending||running)return
  const key=crypto.randomUUID()
  void run(async signal=>{const {data}=await service.call('retryManualQuestion',{}, {signal,key,params:{conversationId:conversation.current,questionId}});signal.throwIfAborted();setTurns(t=>mergeTurns(t,[data]))})
 }
 function older(){if(cursor===null)return;const before=cursor;void run(async signal=>{
  const {data}=await service.call('getQAConversation',undefined,{signal,params:{conversationId:conversation.current},query:{size:20,beforeSequence:before}})
  signal.throwIfAborted();if(data.nextBeforeSequence!==null&&data.nextBeforeSequence>=before)throw new ApiError(0,'INVALID_RESPONSE')
  setTurns(t=>mergeTurns(t,data.turns));setCursor(data.nextBeforeSequence)
 })}
 return {id,turns,cursor,busy,loading,blocked,error,pending,sent,running:!!running,send,retryQuestion,older,reload,
  retry:()=>{if(retry.current)void run(retry.current);else void reload()},dismiss:()=>setError('')}
}
