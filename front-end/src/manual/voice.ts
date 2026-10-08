import type { ManualService } from './service'
import { ManualError, newKey } from './service'
import { pause, waitVisible } from './poll'
export type Recording={blob:Blob;duration:number}
export function validateRecording({blob,duration}:Recording){if(!blob.size||duration<=0)throw new ManualError('RECORDING_EMPTY');if(blob.size>20*1024*1024||!Number.isFinite(duration)||duration>120)throw new ManualError('RECORDING_LIMIT');if(!['audio/webm','audio/mp4','audio/mpeg','audio/wav'].includes(blob.type.split(';')[0]))throw new ManualError('MIC_UNSUPPORTED')}
/** Retains each stage and its key across a lost response; never posts a new answer on a timer. */
export function createVoiceSubmission(service:ManualService,recording:Recording){
 validateRecording(recording)
 const keys={upload:newKey(),transcribe:newKey(),submit:newKey()}
 let mediaId:string|undefined,transcriptionId:string|undefined,ready=false
 const form=new FormData();form.set('purpose','INTERVIEW_AUDIO');const mime=recording.blob.type.split(';')[0];form.set('file',new Blob([recording.blob],{type:mime}),'answer.'+({ 'audio/webm':'webm','audio/mp4':'m4a','audio/mpeg':'mp3','audio/wav':'wav' }[mime]??'audio'))
 return {key:keys.submit,async input(signal:AbortSignal){
  if(!mediaId)mediaId=(await service.call('uploadManualMedia',{},form,{signal,key:keys.upload})).data.id
  if(!transcriptionId){const result=await service.call('createManualTranscription',{}, {mediaId},{signal,key:keys.transcribe});transcriptionId=result.data.id}
  while(!ready){await waitVisible(signal);const result=await service.call('getManualTranscription',{transcriptionId},undefined,{signal});const t=result.data;if(t.mediaId!==mediaId)throw new ManualError('INVALID_RESPONSE');if(t.status==='ERROR'){keys.transcribe=newKey();transcriptionId=undefined;throw new ManualError('TRANSCRIPTION_FAILED')}if(t.status==='READY'){if(!t.text?.trim())throw new ManualError('RECORDING_EMPTY');ready=true;break}await pause(result.retryAfterMs,signal)}
  return {method:'VOICE' as const,transcriptionId:transcriptionId!}
 }}
}
