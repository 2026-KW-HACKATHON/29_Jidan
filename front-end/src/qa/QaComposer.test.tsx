import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import '../manual/dialogTestSetup'
import {QaComposer} from './QaComposer'
import {recordVoice} from '../manual/recorder'
import type {QaService} from './service'
vi.mock('../manual/recorder',()=>({recordVoice:vi.fn()}))
afterEach(cleanup)
beforeEach(()=>{
 vi.mocked(recordVoice).mockReset()
 vi.stubGlobal('URL',class extends URL {static createObjectURL=vi.fn(()=> 'blob:photo');static revokeObjectURL=vi.fn()})
})
const result=(data:unknown)=>({data,retryAfterMs:2000})
it('사진 3장 제한과 업로드·삭제·질문 연결을 처리한다',async()=>{
 let index=0
 const call=vi.fn(async(name)=>result(name==='uploadQAQuestionMedia'?{id:`photo-${++index}`}:undefined)),onSend=vi.fn()
 render(<QaComposer service={{storeId:'store',call} as QaService} disabled={false} onSend={onSend} onAccessLost={()=>{}}/>)
 for(let i=1;i<=3;i++){
  fireEvent.change(screen.getByLabelText('질문 사진 file'),{target:{files:[new File(['photo'],`${i}.png`,{type:'image/png'})]}})
  await screen.findByAltText(`첨부 사진 ${i}`)
 }
 expect(screen.getByRole('button',{name:'카메라'})).toBeDisabled()
 fireEvent.click(screen.getByRole('button',{name:'사진 2 삭제'}));await waitFor(()=>expect(screen.queryByAltText('첨부 사진 3')).not.toBeInTheDocument())
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'사진 속 기기는 어떻게 써요?'}});fireEvent.click(screen.getByRole('button',{name:'보내기'}))
 expect(onSend).toHaveBeenCalledWith({kind:'TEXT',text:'사진 속 기기는 어떻게 써요?',transcriptionId:null,imageMediaIds:['photo-1','photo-3']})
})
it('녹음 전사를 확인한 뒤 VOICE로 전송하고 수정하면 TEXT로 전송한다',async()=>{
 vi.mocked(recordVoice).mockResolvedValue({done:Promise.resolve({blob:new Blob(['voice'],{type:'audio/webm'}),duration:3}),finish:vi.fn(),cancel:vi.fn()})
 const call=vi.fn(async(name)=>result(name==='uploadQAQuestionMedia'?{id:'media'}:name==='transcribeQAQuestionAudio'?{id:'transcript'}:{id:'transcript',mediaId:'media',status:'READY',text:'세척 방법'})),onSend=vi.fn()
 render(<QaComposer service={{storeId:'store',call} as QaService} disabled={false} onSend={onSend} onAccessLost={()=>{}}/>)
 fireEvent.click(screen.getByRole('button',{name:'마이크'}));await waitFor(()=>expect(screen.getByRole('textbox')).toHaveValue('세척 방법'))
 expect(onSend).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'보내기'}))
 expect(onSend).toHaveBeenLastCalledWith({kind:'VOICE',text:null,transcriptionId:'transcript',imageMediaIds:[]})
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'마감 세척 방법'}});fireEvent.click(screen.getByRole('button',{name:'보내기'}))
 expect(onSend).toHaveBeenLastCalledWith({kind:'TEXT',text:'마감 세척 방법',transcriptionId:null,imageMediaIds:[]})
})
it('잘못된 이미지와 사진만 있는 질문을 보내지 않는다',async()=>{
 const call=vi.fn(),onSend=vi.fn()
 render(<QaComposer service={{storeId:'store',call} as QaService} disabled={false} onSend={onSend} onAccessLost={()=>{}}/>)
 fireEvent.change(screen.getByLabelText('질문 사진 file'),{target:{files:[new File(['svg'],'bad.svg',{type:'image/svg+xml'})]}})
 await screen.findByText('JPG·PNG·WebP 사진을 10 MiB 이하로 첨부해 주세요.')
 expect(call).not.toHaveBeenCalled();expect(onSend).not.toHaveBeenCalled()
})
it('사진 업로드 응답 유실은 같은 키로 복구하고 전송을 잠근다',async()=>{
 const call=vi.fn().mockRejectedValueOnce(Error('network')).mockResolvedValue(result({id:'photo'}))
 render(<QaComposer service={{storeId:'store',call} as QaService} disabled={false} onSend={()=>{}} onAccessLost={()=>{}}/>)
 fireEvent.change(screen.getByLabelText('질문 사진 file'),{target:{files:[new File(['photo'],'p.png',{type:'image/png'})]}})
 fireEvent.click(await screen.findByRole('button',{name:'다시 시도'}));await screen.findByAltText('첨부 사진 1')
 expect(call.mock.calls[0][2].key).toBe(call.mock.calls[1][2].key)
})
