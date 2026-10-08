import {expect,it,vi} from 'vitest'
import {transcribe,upload} from './media'
import type {QaService} from './service'
const signal=()=>new AbortController().signal
const recording={blob:new Blob(['voice'],{type:'audio/webm;codecs=opus'}),duration:5}
it('음성 응답 유실 후 업로드를 반복하지 않고 같은 전사 키로 재시도한다',async()=>{
 let starts=0
 const call=vi.fn(async(name)=>{
  if(name==='uploadQAQuestionMedia')return {retryAfterMs:2000,data:{id:'media'}}
  if(name==='transcribeQAQuestionAudio'){if(++starts===1)throw Error('network');return {retryAfterMs:2000,data:{id:'transcript'}}}
  return {retryAfterMs:2000,data:{id:'transcript',mediaId:'media',status:'READY',text:'질문'}}
 })
 const task=transcribe({storeId:'store',call} as QaService,recording)
 await expect(task(signal())).rejects.toThrow();expect(await task(signal())).toEqual({text:'질문',transcriptionId:'transcript'})
 expect(call.mock.calls.filter(c=>c[0]==='uploadQAQuestionMedia')).toHaveLength(1)
 const requests=call.mock.calls.filter(c=>c[0]==='transcribeQAQuestionAudio') as unknown as [string,unknown,{key:string}][]
 expect(requests[0][2].key).toBe(requests[1][2].key)
})
it('전사 ERROR는 전용 재시도로 복구하며 재시도 응답 유실도 같은 키를 사용한다',async()=>{
 let failed=true, retries=0
 const call=vi.fn(async(name)=>{
  if(name==='uploadQAQuestionMedia')return {retryAfterMs:2000,data:{id:'media'}}
  if(name==='transcribeQAQuestionAudio')return {retryAfterMs:2000,data:{id:'transcript'}}
  if(name==='retryQAQuestionTranscription'){if(++retries===1)throw Error('network');failed=false;return {retryAfterMs:2000,data:{id:'transcript'}}}
  return {retryAfterMs:2000,data:{mediaId:'media',status:failed?'ERROR':'READY',text:failed?null:'질문'}}
 })
 const task=transcribe({storeId:'store',call} as QaService,recording)
 await expect(task(signal())).rejects.toThrow();await expect(task(signal())).rejects.toThrow();expect((await task(signal())).text).toBe('질문')
 const requests=call.mock.calls.filter(c=>c[0]==='retryQAQuestionTranscription') as unknown as [string,unknown,{key:string}][]
 expect(requests[0][2].key).toBe(requests[1][2].key)
})
it('2분 초과·빈 녹음을 거절하고 전사 미디어 귀속을 확인한다',async()=>{
 const call=vi.fn(async(name)=>({retryAfterMs:2000,data:name==='uploadQAQuestionMedia'?{id:'media'}:name==='transcribeQAQuestionAudio'?{id:'transcript'}:{mediaId:'other',status:'READY',text:'질문'}})),service={storeId:'store',call} as QaService
 for(const value of [{...recording,duration:121},{...recording,blob:new Blob()}])expect(()=>transcribe(service,value)).toThrow()
 expect(call).not.toHaveBeenCalled();await expect(transcribe(service,recording)(signal())).rejects.toMatchObject({code:'INVALID_RESPONSE'})
})
it('사진 업로드의 재시도는 같은 FormData와 키를 사용한다',async()=>{
 const call=vi.fn().mockRejectedValueOnce(Error('network')).mockResolvedValue({retryAfterMs:2000,data:{id:'photo'}})
 const task=upload({storeId:'store',call} as QaService,new Blob(['photo'],{type:'image/png'}),'QUESTION_IMAGE')
 await expect(task(signal())).rejects.toThrow();await task(signal())
 expect(call.mock.calls[0][1]).toBe(call.mock.calls[1][1]);expect(call.mock.calls[0][2].key).toBe(call.mock.calls[1][2].key)
})
