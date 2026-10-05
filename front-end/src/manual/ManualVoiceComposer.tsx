import { useEffect,useRef,useState } from 'react'
import { Button } from '../ui/Button'
import { recordVoice,type ActiveRecording } from './recorder'
import { errorMessage } from './service'
import type { Recording } from './voice'
export function ManualVoiceComposer({onRecording,disabled=false,label='말해서 답하기'}:{onRecording:(recording:Recording,signal:AbortSignal)=>Promise<void>;disabled?:boolean;label?:string}){
 const [state,setState]=useState<'idle'|'starting'|'recording'|'processing'|'error'>('idle'),[elapsed,setElapsed]=useState(0),[error,setError]=useState(''),[hasSaved,setHasSaved]=useState(false)
 const active=useRef<ActiveRecording|null>(null),controller=useRef<AbortController|null>(null),saved=useRef<Recording|null>(null)
 useEffect(()=>()=>{controller.current?.abort();active.current?.cancel()},[])
 async function submit(recording:Recording,request:AbortController){setState('processing');try{await onRecording(recording,request.signal);if(!request.signal.aborted){saved.current=null;setHasSaved(false);setState('idle')}}catch(e){if(!request.signal.aborted){setError(errorMessage(e));setState('error')}}}
 async function start(){if(controller.current&&!controller.current.signal.aborted&&['starting','recording','processing'].includes(state))return;controller.current?.abort();const request=new AbortController();controller.current=request;saved.current=null;setHasSaved(false);setElapsed(0);setError('');setState('starting')
  try{const recorder=await recordVoice(request.signal,setElapsed);active.current=recorder;if(request.signal.aborted){recorder.cancel();return}setState('recording');const recording=await recorder.done;if(request.signal.aborted)return;active.current=null;saved.current=recording;setHasSaved(true);await submit(recording,request)}catch(e){if(!request.signal.aborted){setError(errorMessage(e));setState('error')}}
 }
 function cancel(){controller.current?.abort();active.current?.cancel();active.current=null;saved.current=null;setHasSaved(false);setState('idle');setError('')}
 function retry(){if(!saved.current)return;const request=new AbortController();controller.current=request;void submit(saved.current,request)}
 return <div className="manual-voice" aria-live="polite">
  {state==='recording'?<><p className="manual-voice-title">● 답변을 듣고 있어요</p><p className="manual-muted">{String(Math.floor(elapsed/60)).padStart(2,'0')}:{String(elapsed%60).padStart(2,'0')} · 말씀을 마치면 아래 버튼을 눌러주세요.</p><Button onClick={()=>active.current?.finish()}>답변 마치기</Button><Button intent="secondary" onClick={cancel}>녹음 취소</Button></>:state==='starting'?<><p role="status">마이크를 준비하고 있어요</p><Button intent="secondary" onClick={cancel}>녹음 취소</Button></>:state==='processing'?<><p className="manual-voice-title" role="status">답변을 정리하고 있어요</p><p className="manual-muted">잠시만 기다려 주세요.<br/>정리한 내용을 곧 보여드릴게요.</p></>:<>{error&&<p role="alert">{error}</p>}{state==='error'&&hasSaved&&<Button disabled={disabled} onClick={retry}>다시 시도</Button>}<Button disabled={disabled} onClick={()=>void start()}>{state==='error'?'다시 녹음하기':label}</Button></>}
 </div>
}
