import { ManualError } from './service'
import type { Recording } from './voice'
export type ActiveRecording={done:Promise<Recording>;finish:()=>void;cancel:()=>void}
export async function recordVoice(signal:AbortSignal,tick:(seconds:number)=>void):Promise<ActiveRecording>{
 if(!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder==='undefined')throw new ManualError('MIC_UNSUPPORTED')
 signal.throwIfAborted()
 let stream:MediaStream
 try{stream=await navigator.mediaDevices.getUserMedia({audio:true})}catch{if(signal.aborted)throw signal.reason;throw new ManualError('MIC_DENIED')}
 if(signal.aborted){stream.getTracks().forEach(t=>t.stop());throw signal.reason}
 const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t=>MediaRecorder.isTypeSupported(t))
 if(!mime){stream.getTracks().forEach(t=>t.stop());throw new ManualError('MIC_UNSUPPORTED')}
 let recorder:MediaRecorder
 try{recorder=new MediaRecorder(stream,{mimeType:mime})}catch{stream.getTracks().forEach(t=>t.stop());throw new ManualError('MIC_UNSUPPORTED')}
 const started=performance.now(),chunks:Blob[]=[];let bytes=0,settled=false,finishing=false,seconds=0
 let resolve!:(recording:Recording)=>void,reject!:(error:unknown)=>void
 const done=new Promise<Recording>((res,rej)=>{resolve=res;reject=rej})
 let timer:ReturnType<typeof setInterval>|undefined,context:AudioContext|undefined,analyser:AnalyserNode|undefined,peak=0
 try{if(typeof AudioContext!=='undefined'){context=new AudioContext();analyser=context.createAnalyser();context.createMediaStreamSource(stream).connect(analyser)}}catch{void context?.close();context=undefined;analyser=undefined}
 const cleanup=()=>{if(timer)clearInterval(timer);signal.removeEventListener('abort',cancel);stream.getTracks().forEach(t=>t.stop());void context?.close();recorder.ondataavailable=null;recorder.onerror=null;recorder.onstop=null}
 const fail=(error:unknown)=>{if(settled)return;settled=true;if(recorder.state!=='inactive')recorder.stop();cleanup();reject(error)}
 const cancel=()=>fail(signal.reason??new DOMException('Aborted','AbortError'))
 const finish=()=>{if(settled||finishing)return;finishing=true;seconds=Math.min(120,(performance.now()-started)/1000);recorder.stop()}
 recorder.ondataavailable=event=>{if(settled)return;bytes+=event.data.size;if(bytes>20*1024*1024){fail(new ManualError('RECORDING_LIMIT'));return}chunks.push(event.data)}
 recorder.onerror=()=>fail(new ManualError('RECORDING_FAILED'))
 recorder.onstop=()=>{if(settled)return;settled=true;const blob=new Blob(chunks,{type:mime});const silent=analyser && peak<0.005;cleanup();if(!blob.size || seconds<=0 || silent)reject(new ManualError('RECORDING_EMPTY'));else resolve({blob,duration:seconds})}
 signal.addEventListener('abort',cancel,{once:true})
 try{recorder.start(250)}catch(error){fail(error);return {done,finish,cancel}}
 timer=setInterval(()=>{const elapsed=Math.min(120,(performance.now()-started)/1000);tick(Math.floor(elapsed));if(analyser){const samples=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(samples);peak=Math.max(peak,Math.sqrt(samples.reduce((sum,n)=>sum+n*n,0)/samples.length))}if(elapsed>=120)finish()},100)
 return {done,finish,cancel}
}
