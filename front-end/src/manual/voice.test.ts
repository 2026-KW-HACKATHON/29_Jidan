import { expect,it,vi } from 'vitest'
import { createVoiceSubmission,validateRecording } from './voice'
import type { ManualService } from './service'
const id='11111111-1111-4111-8111-111111111111'
const recording={blob:new Blob(['sound'],{type:'audio/webm;codecs=opus'}),duration:3}
it('상한은 허용하고 빈 녹음·초과·잘못된 MIME을 거절한다',()=>{
 expect(()=>validateRecording({blob:new Blob([new Uint8Array(20*1024*1024)],{type:'audio/webm'}),duration:120})).not.toThrow()
 for(const r of [{...recording,duration:121},{...recording,duration:NaN},{...recording,duration:0},{...recording,blob:new Blob([],{type:'audio/webm'})},{...recording,blob:new Blob(['a'],{type:'text/plain'})},{...recording,blob:new Blob([new Uint8Array(20*1024*1024+1)],{type:'audio/mp4'})}])expect(()=>validateRecording(r)).toThrow()
})
it('READY 후 VOICE를 만들며 응답 유실 후 같은 키·본문을 재사용한다',async()=>{
 const call=vi.fn().mockRejectedValueOnce(Error('network')).mockResolvedValueOnce({data:{id},retryAfterMs:2000}).mockResolvedValueOnce({data:{id,mediaId:id,status:'READY'},retryAfterMs:2000}).mockResolvedValue({data:{id,mediaId:id,status:'READY',text:'원문 표시 금지'},retryAfterMs:2000})
 const submission=createVoiceSubmission({storeId:id,call} as ManualService,recording),signal=new AbortController().signal
 await expect(submission.input(signal)).rejects.toThrow('network')
 expect(await submission.input(signal)).toEqual({method:'VOICE',transcriptionId:id})
 expect(await submission.input(signal)).toEqual({method:'VOICE',transcriptionId:id})
 expect(call).toHaveBeenCalledTimes(4);expect(call.mock.calls[0][3].key).toBe(call.mock.calls[1][3].key)
 expect(call.mock.calls[1][2].get('purpose')).toBe('INTERVIEW_AUDIO');expect(call.mock.calls[1][2].get('file').type).toBe('audio/webm')
})
it('실패 전사는 새 키로 재시도하고 미디어를 다시 업로드하지 않는다',async()=>{
 const call=vi.fn().mockResolvedValueOnce({data:{id},retryAfterMs:2000}).mockResolvedValueOnce({data:{id},retryAfterMs:2000}).mockResolvedValueOnce({data:{id,mediaId:id,status:'ERROR'},retryAfterMs:2000}).mockResolvedValueOnce({data:{id},retryAfterMs:2000}).mockResolvedValueOnce({data:{id,mediaId:id,status:'READY',text:'응답'},retryAfterMs:2000})
 const submission=createVoiceSubmission({storeId:id,call} as ManualService,recording),signal=new AbortController().signal
 await expect(submission.input(signal)).rejects.toThrow('TRANSCRIPTION_FAILED');await submission.input(signal)
 expect(call.mock.calls.filter(c=>c[0]==='uploadManualMedia')).toHaveLength(1);expect(call.mock.calls[1][3].key).not.toBe(call.mock.calls[3][3].key)
})
