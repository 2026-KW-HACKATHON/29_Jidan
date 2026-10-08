import './dialogTestSetup'
import {cleanup,render,screen,fireEvent} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualAuthoring} from './ManualAuthoring'
import {createManualPreviewService} from '../dev/manualPreviewService'
afterEach(cleanup)
it('시작 안내는 음성만 제공하고 서버 세션으로 이어 쓴다',async()=>{const service=createManualPreviewService();const call=vi.spyOn(service,'call');render(<ManualAuthoring service={service} onBack={vi.fn()}/>);await screen.findByRole('button',{name:'인터뷰 시작하기'});expect(screen.queryByRole('textbox')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'인터뷰 시작하기'}));await screen.findByRole('button',{name:'말해서 답하기'});expect(call).toHaveBeenCalledWith('startManualInterview',{}, {},expect.objectContaining({key:expect.any(String)}))})
it('이어하기는 기존 세션을 읽고 새 인터뷰를 시작하지 않는다',async()=>{const service=createManualPreviewService(true);const call=vi.spyOn(service,'call');render(<ManualAuthoring service={service} onBack={vi.fn()}/>);await screen.findByRole('button',{name:'말해서 답하기'});expect(call.mock.calls.some(([name])=>name==='startManualInterview')).toBe(false);expect(screen.queryByText('샘플 음성 답변')).not.toBeInTheDocument()})
