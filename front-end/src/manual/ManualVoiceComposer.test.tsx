import './dialogTestSetup'
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {ManualError} from './service'
const {recording}=vi.hoisted(()=>({recording:{blob:new Blob(['voice'],{type:'audio/webm'}),duration:3}}))
vi.mock('./recorder',()=>({recordVoice:vi.fn(async()=>({done:Promise.resolve(recording),finish:vi.fn(),cancel:vi.fn()}))}))
afterEach(cleanup)
it('응답 유실은 같은 녹음을 다시 제출하고 전사 확인 입력을 만들지 않는다',async()=>{
 const onRecording=vi.fn().mockRejectedValueOnce(Error('lost')).mockResolvedValueOnce(undefined)
 render(<ManualVoiceComposer onRecording={onRecording}/>);fireEvent.click(screen.getByRole('button',{name:'말해서 답하기'}));fireEvent.click(await screen.findByRole('button',{name:'다시 시도'}));await waitFor(()=>expect(onRecording).toHaveBeenCalledTimes(2))
 expect(onRecording.mock.calls[0][0]).toBe(onRecording.mock.calls[1][0]);expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
})
it('충돌에는 오래된 녹음 재전송 대신 새 녹음을 제공한다',async()=>{
 const onRecording=vi.fn().mockRejectedValue(new ManualError('REVISION_CONFLICT'))
 render(<ManualVoiceComposer onRecording={onRecording}/>);fireEvent.click(screen.getByRole('button',{name:'말해서 답하기'}));await screen.findByRole('alertdialog');expect(screen.queryByRole('button',{name:'다시 시도'})).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'다시 녹음하기'})).toBeEnabled()
})
