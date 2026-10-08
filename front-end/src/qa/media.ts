import {ApiError} from '../api/client'
import {validateRecording,type Recording} from '../manual/voice'
import {pause,waitVisible} from '../manual/poll'
import type {QaService} from './service'
export function upload(service:QaService,file:Blob,purpose:'QUESTION_IMAGE'|'QUESTION_AUDIO') {
 const key=crypto.randomUUID(),form=new FormData(),mime=file.type.split(';')[0]
 form.set('purpose',purpose);form.set('file',new Blob([file],{type:mime}),'question.'+({'image/png':'png','image/jpeg':'jpg','image/webp':'webp','audio/webm':'webm','audio/mp4':'m4a','audio/mpeg':'mp3','audio/wav':'wav'}[mime]??'bin'))
 return async(signal:AbortSignal)=>(await service.call('uploadQAQuestionMedia',form,{signal,key})).data
}
/** A retry resumes the failed stage with its original key, without duplicate uploads/transcriptions. */
export function transcribe(service:QaService,recording:Recording){
 validateRecording(recording)
 const put=upload(service,recording.blob,'QUESTION_AUDIO'),startKey=crypto.randomUUID()
 let mediaId='',transcriptionId='',retryKey='',failed=false
 return async(signal:AbortSignal)=>{
  if(!mediaId)mediaId=(await put(signal)).id
  signal.throwIfAborted()
  if(!transcriptionId)transcriptionId=(await service.call('transcribeQAQuestionAudio',{mediaId},{signal,key:startKey})).data.id
  if(failed){
   if(!retryKey)retryKey=crypto.randomUUID()
   await service.call('retryQAQuestionTranscription',{}, {signal,key:retryKey,params:{transcriptionId}})
   failed=false;retryKey=''
  }
  while(true){
   await waitVisible(signal)
   const {data,retryAfterMs}=await service.call('getQAQuestionTranscription',undefined,{signal,params:{transcriptionId}})
   signal.throwIfAborted()
   if(data.mediaId!==mediaId)throw new ApiError(0,'INVALID_RESPONSE')
   if(data.status==='ERROR'){failed=true;throw new ApiError(0,'TRANSCRIPTION_FAILED')}
   if(data.status==='READY'){
    if(!data.text.trim()||data.text.length>2000)throw new ApiError(0,'VALIDATION_ERROR')
    return {text:data.text,transcriptionId}
   }
   await pause(retryAfterMs,signal)
  }
 }
}
