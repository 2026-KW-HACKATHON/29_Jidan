import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {ApplicationDialog} from './ApplicationDialog'
import {sampleJobs} from '../dev/jobFixtures'
import {mockDialog} from '../jobs/dialogTestSupport'
import type {Application,ApplicationService} from './model'
mockDialog()
const job=sampleJobs[0]
function mount(service:ApplicationService,onSuccess=vi.fn(),onClose=vi.fn()) {const view=render(<ApplicationDialog job={job} service={service} onSuccess={onSuccess} onClose={onClose}/>);return {view,onSuccess,onClose}}
it('빈 값과 공백은 금지하고 501자 붙여넣기·한글 조합을 제한한다',()=>{
  mount({submit:vi.fn(),withdraw:vi.fn()})
  const input=screen.getByLabelText('지원자 자기소개 *'),button=screen.getByRole('button',{name:'지원 완료하기'})
  expect(input).toHaveValue('');expect(button).toBeDisabled()
  fireEvent.change(input,{target:{value:' \n '}});expect(button).toBeDisabled()
  fireEvent.change(input,{target:{value:'가'.repeat(501)}});expect(input).toHaveValue('가'.repeat(500));expect(screen.getByText('500 / 500')).toBeInTheDocument()
  fireEvent.compositionStart(input);fireEvent.change(input,{target:{value:'나'.repeat(501)}});expect(button).toBeEnabled()
  fireEvent.compositionEnd(input);expect(input).toHaveValue('나'.repeat(500));expect(button).toBeEnabled()
})
it('중복 제출을 막고 실패 시 입력을 보존하여 재시도한다',async()=>{
  let resolve!: (app:Application)=>void
  const submit=vi.fn().mockRejectedValueOnce(Error('MOCK')).mockImplementation(()=>new Promise<Application>(r=>{resolve=r}))
  const {onSuccess}=mount({submit,withdraw:vi.fn()})
  fireEvent.change(screen.getByLabelText('지원자 자기소개 *'),{target:{value:'경력이 있어요'}})
  fireEvent.click(screen.getByRole('button',{name:'지원 완료하기'}))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('지원자 자기소개 *')).toHaveValue('경력이 있어요')
  fireEvent.click(screen.getByRole('button',{name:'지원 완료하기'}))
  fireEvent.submit(screen.getByLabelText('지원자 자기소개 *').closest('form')!)
  expect(submit).toHaveBeenCalledTimes(2)
  await act(async()=>resolve({id:'1',job,introduction:'경력이 있어요'}))
  await waitFor(()=>expect(onSuccess).toHaveBeenCalledOnce())
})
it('닫힌 화면에는 늦은 완료 결과를 반영하지 않는다',async()=>{
  let resolve!: (app:Application)=>void
  const submit=vi.fn<ApplicationService['submit']>(()=>new Promise<Application>(r=>{resolve=r}))
  const {view,onSuccess}=mount({submit,withdraw:vi.fn()})
  fireEvent.change(screen.getByLabelText('지원자 자기소개 *'),{target:{value:'지원해요'}})
  fireEvent.click(screen.getByRole('button',{name:'지원 완료하기'}));const signal=submit.mock.calls[0]?.[2] as AbortSignal|undefined
  view.unmount()
  await act(async()=>resolve({id:'1',job,introduction:'지원해요'}))
  expect(signal?.aborted).toBe(true);expect(onSuccess).not.toHaveBeenCalled()
})
it('응답 없는 서비스는 대기 종료 후 입력을 유지하고 재시도할 수 있다',async()=>{
  vi.useFakeTimers()
  try {
    const submit=vi.fn<ApplicationService['submit']>().mockImplementationOnce(()=>new Promise(()=>{})).mockResolvedValue({id:'1',job,introduction:'지원해요'})
    const {view,onSuccess}=mount({submit,withdraw:vi.fn()})
    fireEvent.change(screen.getByLabelText('지원자 자기소개 *'),{target:{value:'지원해요'}})
    fireEvent.click(screen.getByRole('button',{name:'지원 완료하기'}))
    await act(async()=>{await vi.advanceTimersByTimeAsync(10000)})
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'지원 완료하기'})).toBeEnabled()
    fireEvent.click(screen.getByRole('button',{name:'지원 완료하기'}));await act(async()=>{})
    expect(onSuccess).toHaveBeenCalledOnce();view.unmount()
  } finally {vi.useRealTimers()}
})

it('한글 조합 중 직접 완료 클릭은 마지막 글자를 포함하고 조합 중 암묵 제출은 막는다',async()=>{
 const submit=vi.fn().mockResolvedValue({id:'1',job,introduction:'안녕하세요'})
 mount({submit,withdraw:vi.fn()})
 const input=screen.getByLabelText('지원자 자기소개 *')
 fireEvent.compositionStart(input);fireEvent.change(input,{target:{value:'안녕하세요'}})
 const button=screen.getByRole('button',{name:'지원 완료하기'});expect(button).toBeEnabled()
 fireEvent.submit(input.closest('form')!);expect(submit).not.toHaveBeenCalled()
 fireEvent.click(button)
 await waitFor(()=>expect(submit).toHaveBeenCalledOnce())
 expect(submit).toHaveBeenCalledWith(job,'안녕하세요',expect.any(AbortSignal))
})
