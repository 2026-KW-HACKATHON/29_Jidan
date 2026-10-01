import { act, waitFor, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach,beforeEach,expect,it,vi } from 'vitest'
import { Employment, type AccessService } from './Employment'
const original=Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(()=>{Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value(this:HTMLDialogElement){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value(this:HTMLDialogElement){this.removeAttribute('open')}})})
afterEach(()=>{cleanup();vi.useRealTimers();for(const key of ['showModal','close']){if(original[key])Object.defineProperty(HTMLDialogElement.prototype,key,original[key]);else Reflect.deleteProperty(HTMLDialogElement.prototype,key)}})
const data={name:'김지수',job:'홀 서빙 · 정기 근무자',type:'정기 근무',start:'2025. 07. 01',expiry:'별도 종료 전까지',permissions:['업무 매뉴얼','체크리스트','AI 질의응답']}
function setup(end:AccessService['end']=vi.fn().mockResolvedValue(undefined)){render(<Employment data={data} service={{end}} onBack={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'접근 종료 처리'}));return end}
it('취소하면 상태를 유지하고 트리거에 포커스를 복귀한다',async()=>{const end=setup();const dialog=screen.getByRole('alertdialog');expect(screen.getByRole('button',{name:'취소'})).toHaveFocus();fireEvent.keyDown(dialog,{key:'Tab'});expect(screen.getByRole('button',{name:'접근 종료'})).toHaveFocus();fireEvent.click(dialog);expect(screen.getByRole('alertdialog')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'취소'}));await act(async()=>{});expect(screen.getByText('재직 중')).toBeVisible();expect(screen.getByRole('button',{name:'접근 종료 처리'})).toHaveFocus();expect(end).not.toHaveBeenCalled()})
it('실패 후 같은 작업 키로 재시도하고 성공 후만 상태를 변경한다', async () => {
  const end = vi.fn().mockRejectedValueOnce(Error()).mockResolvedValueOnce(undefined)
  setup(end)
  fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
  // 제목의 DOM 삽입뿐 아니라 showModal 이후 접근 가능한 상태까지 기다린다.
  const errorDialog = await screen.findByRole('alertdialog', { name: '요청을 처리하지 못했어요' })
  const retry = await within(errorDialog).findByRole('button', { name: '다시 시도' })
  expect(retry).toBeEnabled()
  expect(screen.getByText('재직 중')).toBeVisible()
  fireEvent.click(retry)
  const completeDialog = await screen.findByRole('dialog', { name: '접근 종료가 완료됐어요' })
  expect(screen.queryByText('재직 중')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '접근 종료 완료' })).toBeDisabled()
  expect(end).toHaveBeenCalledTimes(2)
  expect(end.mock.calls[0][0]).toBe(end.mock.calls[1][0])
  fireEvent.click(within(completeDialog).getByRole('button', { name: '확인' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})
it('중복 제출과 취소 이후 늦은 성공을 무시한다',async()=>{let resolve!:()=>void;const end=vi.fn((_key:string,_signal:AbortSignal)=>new Promise<void>(r=>{resolve=r}));setup(end);const button=screen.getByRole('button',{name:'접근 종료'});fireEvent.click(button);fireEvent.click(button);expect(end).toHaveBeenCalledTimes(1);fireEvent.click(screen.getByRole('button',{name:'취소'}));await act(async()=>resolve());expect(end.mock.calls[0][1].aborted).toBe(true);expect(screen.getByText('재직 중')).toBeVisible();expect(screen.queryByText('접근 종료가 완료됐어요')).not.toBeInTheDocument()})

it('시간 제한 후 기존 상태를 유지하고 재시도에서만 성공한다', async () => {
  let resolve!: () => void
  const end = vi.fn().mockImplementationOnce(() => new Promise<void>(r => { resolve = r })).mockResolvedValueOnce(undefined)
  setup(end)
  vi.useFakeTimers()
  fireEvent.click(screen.getByRole('button', { name: '접근 종료' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(screen.getByText('요청을 처리하지 못했어요')).toBeVisible()
  expect(screen.getByText('재직 중')).toBeVisible()
  await act(async () => resolve())
  expect(screen.queryByText('접근 종료가 완료됐어요')).not.toBeInTheDocument()
  vi.useRealTimers()
  const errorDialog = await screen.findByRole('alertdialog', { name: '요청을 처리하지 못했어요' })
  fireEvent.click(await within(errorDialog).findByRole('button', { name: '다시 시도' }))
  const completeDialog = await screen.findByRole('dialog', { name: '접근 종료가 완료됐어요' })
  expect(end).toHaveBeenCalledTimes(2)
  expect(end.mock.calls[0][0]).toBe(end.mock.calls[1][0])
  fireEvent.click(within(completeDialog).getByRole('button', { name: '확인' }))
  await waitFor(() => expect(screen.getByRole('heading', { name: '김지수' })).toHaveFocus())
})
