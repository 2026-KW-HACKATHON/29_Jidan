import { cleanup, fireEvent, render, screen, act, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { WorkerProfile } from './WorkerProfile'
import { emptyWorker } from '../registration/worker/model'
import type { ProfileService } from './service'
const initialProfile={email:'member@example.com',draft:{...emptyWorker,name:'김지수',phone:'010-1234-5678',birth:'2001-03-14',gender:'여성' as const,experience:'신입' as const,availability:[{id:'a',days:[0],start:540,end:840,overnight:false}]}}
afterEach(()=>{cleanup();vi.useRealTimers()})
function setup(save:ProfileService['save']=vi.fn().mockResolvedValue(undefined)) {render(<WorkerProfile initialProfile={initialProfile} service={{read:vi.fn(),save}} onBack={vi.fn()}/>);return save}
it('취소는 수정 복사본을 버리고 원본과 이메일을 유지한다',()=>{setup();fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));expect(screen.getByLabelText('이메일 | Google 연동')).toHaveAttribute('readonly');fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}});fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('김지수 님')).toBeVisible();expect(initialProfile.draft.name).toBe('김지수')})
it('실패는 편집 내용을 보존하고 재시도 성공 후 반영한다',async()=>{const save=vi.fn().mockRejectedValueOnce(Error()).mockResolvedValueOnce(undefined);setup(save);fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}});fireEvent.click(screen.getByRole('button',{name:'변경 사항 저장'}));await screen.findByRole('alert');expect(screen.getByLabelText('이름 *')).toHaveValue('Alex Kim');fireEvent.click(screen.getByRole('button',{name:'변경 사항 저장'}));expect(await screen.findByText('Alex Kim 님')).toBeVisible();expect(save).toHaveBeenCalledTimes(2);expect(initialProfile.draft.name).toBe('김지수')})
it('유효하지 않은 정보는 저장하지 않는다',()=>{const save=setup();fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:''}});fireEvent.click(screen.getByRole('button',{name:'변경 사항 저장'}));expect(save).not.toHaveBeenCalled()})
it('중복 저장을 막고 대기 중 취소 후 늦은 성공을 무시한다',async()=>{let resolve!:()=>void;const save=vi.fn((_value, _signal:AbortSignal)=>new Promise<void>(r=>{resolve=r}));setup(save);fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}});const button=screen.getByRole('button',{name:'변경 사항 저장'});fireEvent.click(button);fireEvent.click(button);expect(save).toHaveBeenCalledTimes(1);fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));await act(async()=>resolve());expect(screen.getByText('김지수 님')).toBeVisible();expect(save.mock.calls[0][1].aborted).toBe(true)})
it('시간 수정 후 섹션 취소는 원래 시간을 복구한다', () => {
  const save = setup()
  fireEvent.click(screen.getByRole('button', { name: '가능한 시간 수정' }))
  // 시간 셀의 aria-label로 조회해 252개 버튼의 접근성 계산 반복을 줄인다.
  const slot = screen.getByLabelText('월 09:00–09:30')
  expect(slot).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(slot)
  expect(slot).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByText('선택한 시간 · 주 4.5시간')).toBeVisible()
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: '뒤로 가기' }))
  fireEvent.click(screen.getByRole('button', { name: '가능한 시간 수정' }))
  expect(screen.getByLabelText('월 09:00–09:30')).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText('선택한 시간 · 주 5시간')).toBeVisible()
  expect(save).not.toHaveBeenCalled()
  expect(initialProfile.draft.availability).toEqual([{ id: 'a', days: [0], start: 540, end: 840, overnight: false }])
}, 15000)
it('경력 종류 변경과 하위 편집 취소는 섹션 원본을 유지한다',()=>{setup();fireEvent.click(screen.getByRole('button',{name:'근무 정보 수정'}));fireEvent.click(screen.getByLabelText('경력 있음'));fireEvent.click(screen.getByRole('button',{name:'+ 경력 추가'}));fireEvent.change(screen.getByLabelText('담당 업무 *'),{target:{value:'새 업무'}});fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('신입')).toBeVisible();expect(screen.queryByText('새 업무')).not.toBeInTheDocument()})

it('저장이 지연되면 입력을 유지하며 버튼을 복구하고 늦은 성공을 무시한다',async()=>{let resolve!:()=>void;setup(()=>new Promise<void>(r=>{resolve=r}));fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}});vi.useFakeTimers();fireEvent.click(screen.getByRole('button',{name:'변경 사항 저장'}));await act(async()=>{await vi.advanceTimersByTimeAsync(10000)});expect(screen.getByRole('alert')).toBeVisible();expect(screen.getByRole('button',{name:'변경 사항 저장'})).toBeEnabled();await act(async()=>resolve());expect(screen.getByLabelText('이름 *')).toHaveValue('Alex Kim');expect(screen.queryByText('Alex Kim 님')).not.toBeInTheDocument()})
it('하위 경력 저장 이후 섹션 취소도 원래 경력을 복구한다',()=>{const initial={...initialProfile,draft:{...initialProfile.draft,experience:'경력 있음' as const,careers:[{id:'c',industry:'카페' as const,duties:'원래 업무',store:'',start:'2024-03',end:'2025-02',current:false}]}};render(<WorkerProfile initialProfile={initial} onBack={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'근무 정보 수정'}));fireEvent.click(screen.getByRole('button',{name:'경력 수정'}));fireEvent.change(screen.getByLabelText('담당 업무 *'),{target:{value:'변경한 업무'}});fireEvent.click(screen.getByRole('button',{name:'경력 저장'}));expect(screen.getByText('카페 · 변경한 업무')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('카페 · 원래 업무')).toBeVisible();expect(initial.draft.careers[0].duties).toBe('원래 업무')})
