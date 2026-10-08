import {useEffect,useRef,useState} from 'react'
import {ApiError} from '../api/client'
import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
import {ManualPhotoSourceDialog,type PhotoSource} from '../manual/ManualPhotoSourceDialog'
import {recordVoice,type ActiveRecording} from '../manual/recorder'
import {upload,transcribe} from './media'
import {accessLost,qaError} from './errors'
import {validatePhoto,type QaService} from './service'
import type {QAMedia,QAQuestionInput} from './types.generated'

type Task={label:string;execute:(signal:AbortSignal)=>Promise<void>}
export function QaComposer({service,disabled,onSend,onAccessLost}:{service:QaService;disabled:boolean;onSend:(input:QAQuestionInput)=>void;onAccessLost:(error:unknown)=>void}){
 const [text,setText]=useState(''),[transcript,setTranscript]=useState<{text:string;transcriptionId:string}|null>(null)
 const [photos,setPhotos]=useState<{media:QAMedia;file:Blob}[]>([]),[source,setSource]=useState(false)
 const [mode,setMode]=useState(''),[error,setError]=useState(''),[elapsed,setElapsed]=useState(0),[recovery,setRecovery]=useState(false)
 const request=useRef<AbortController|null>(null),recorder=useRef<ActiveRecording|null>(null),task=useRef<Task|null>(null)
 const files=useRef<Partial<Record<PhotoSource,HTMLInputElement|null>>>({})
 useEffect(()=>()=>{request.current?.abort();recorder.current?.cancel()},[])
 const locked=disabled||!!mode||recovery
 async function run(next:Task){
  if(request.current&&!request.current.signal.aborted)return
  const controller=new AbortController();request.current=controller;task.current=next;setMode(next.label);setError('');setRecovery(false)
  try{await next.execute(controller.signal);controller.signal.throwIfAborted();task.current=null}
  catch(e){if(!controller.signal.aborted){if(accessLost(e)){onAccessLost(e);task.current=null}else{setError(qaError(e));setRecovery(true)}}}
  finally{if(request.current===controller){request.current=null;recorder.current=null;setMode('')}}
 }
 function cancel(){request.current?.abort();recorder.current?.cancel();request.current=null;recorder.current=null;task.current=null;setMode('');setError('');setRecovery(false)}
 function add(file:File){
  if(locked)return
  try{if(photos.length>=3)throw new ApiError(0,'QA_PHOTO_LIMIT');validatePhoto(file)}catch(e){setError(qaError(e));return}
  const put=upload(service,file,'QUESTION_IMAGE')
  void run({label:'사진을 올리고 있어요.',execute:async signal=>{const media=await put(signal);signal.throwIfAborted();setPhotos(p=>[...p,{media,file}])}})
 }
 function remove(id:string){
  const key=crypto.randomUUID()
  void run({label:'사진을 지우고 있어요.',execute:async signal=>{await service.call('deleteQAUnattachedMedia',undefined,{signal,key,params:{mediaId:id}});signal.throwIfAborted();setPhotos(p=>p.filter(photo=>photo.media.id!==id))}})
 }
 function start(){
  if(locked)return
  setElapsed(0)
  void run({label:'마이크를 준비하고 있어요.',execute:async signal=>{
   const active=await recordVoice(signal,setElapsed);recorder.current=active;signal.throwIfAborted();setMode('recording')
   const recording=await active.done;signal.throwIfAborted();recorder.current=null
   const convert=transcribe(service,recording)
   const process:Task={label:'음성을 글로 바꾸고 있어요.',execute:async signal=>{const value=await convert(signal);signal.throwIfAborted();setTranscript(value);setText(value.text)}}
   task.current=process;setMode(process.label);await process.execute(signal)
  }})
 }
 function send(){
  if(locked||!text.trim()||text.length>2000)return
  const imageMediaIds=photos.map(p=>p.media.id)
  onSend(transcript&&text===transcript.text?{kind:'VOICE',text:null,transcriptionId:transcript.transcriptionId,imageMediaIds}:{kind:'TEXT',text:text.trim(),transcriptionId:null,imageMediaIds})
 }
 return <form className="qa-composer" onSubmit={e=>{e.preventDefault();send()}}>
  {photos.length>0&&<div className="qa-attachments" aria-label="질문 첨부 사진">{photos.map((p,i)=><div key={p.media.id}><LocalPhoto file={p.file} index={i}/><button type="button" disabled={locked} onClick={()=>remove(p.media.id)} aria-label={`사진 ${i+1} 삭제`}>삭제</button></div>)}</div>}
  <textarea aria-label="궁금한 업무" placeholder="궁금한 업무를 입력해 주세요" value={text} maxLength={2000} disabled={locked} onChange={e=>setText(e.target.value)} rows={2}/>
  {transcript&&<p className="qa-muted">음성을 글로 바꿨어요. 확인하거나 수정한 뒤 보내 주세요.</p>}
  <div className="qa-tools"><Button intent="secondary" disabled={locked||photos.length>=3} onClick={()=>setSource(true)}>카메라</Button><Button intent="secondary" disabled={locked} onClick={start}>마이크</Button><Button type="submit" disabled={locked||!text.trim()}>보내기</Button></div>
  <span className="qa-count">{text.length}/2,000 · 사진 {photos.length}/3</span>
  {recovery&&!error&&<Button intent="secondary" onClick={()=>{if(task.current)void run(task.current)}}>미디어 처리 다시 시도</Button>}
  {(['camera','album','file'] as const).map(kind=><input hidden key={kind} ref={el=>{files.current[kind]=el}} type="file" accept="image/jpeg,image/png,image/webp" capture={kind==='camera'?'environment':undefined} aria-label={`질문 사진 ${kind}`} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)add(file)}}/>)}
  <ManualPhotoSourceDialog open={source} onClose={()=>setSource(false)} onSelect={kind=>{setSource(false);files.current[kind]?.click()}}/>
  <Modal open={!!mode} showIcon={false} title={mode==='recording'?'질문을 듣고 있어요':mode} description={mode==='recording'?`${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')} · 녹음은 최대 2분이에요.`:'잠시만 기다려 주세요.'} confirmLabel={mode==='recording'?'녹음 마치기':'취소'} onClose={cancel} showCancel={mode==='recording'} closeOnConfirm={false} onConfirm={mode==='recording'?()=>recorder.current?.finish():cancel}/>
  <Modal open={!!error} state="error" title="입력을 확인해 주세요" description={error} onClose={()=>setError('')} confirmLabel={recovery?'다시 시도':'확인'} closeOnConfirm={false} onConfirm={()=>{if(task.current)void run(task.current);else setError('')}} summary={recovery?<Button intent="secondary" onClick={cancel}>입력으로 돌아가기</Button>:undefined}/>
 </form>
}
function LocalPhoto({file,index}:{file:Blob;index:number}){
 const image=useRef<HTMLImageElement>(null)
 useEffect(()=>{const next=URL.createObjectURL(file);if(image.current)image.current.src=next;return()=>URL.revokeObjectURL(next)},[file])
 return <img ref={image} alt={`첨부 사진 ${index+1}`}/>
}
