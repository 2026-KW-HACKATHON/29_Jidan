import { act, fireEvent, render, screen, cleanup } from '@testing-library/react'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { ValuePicker } from './ValuePicker'
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.open = true }; HTMLDialogElement.prototype.close = function () { this.open = false } })
afterEach(cleanup)
it('연월의 경계를 제한하고 연도 변경 시 가능한 월로 보정한다', () => {
 const save = vi.fn()
 render(<ValuePicker title="시작 연월" type="month" value="2024-12" min="2023-03" max="2025-02" onClose={()=>{}} onSave={save} />)
 fireEvent.keyDown(screen.getByRole('spinbutton',{name:'연도'}),{key:'End'})
 expect(screen.getByRole('spinbutton',{name:'월'}).getAttribute('aria-valuenow')).toBe('2')
 fireEvent.keyDown(screen.getByRole('spinbutton',{name:'월'}),{key:'ArrowDown'})
 fireEvent.click(screen.getByRole('button',{name:'선택 완료'}))
 expect(save).toHaveBeenCalledWith('2025-02',false)
})
it('자정과 30분 및 다음 날 종료를 확인할 때만 저장한다', () => {
 const save=vi.fn(),close=vi.fn()
 render(<ValuePicker title="종료 시간" type="time" value="1410" overnight={false} onClose={close} onSave={save} />)
 fireEvent.keyDown(screen.getByRole('spinbutton'),{key:'Home'})
 fireEvent.click(screen.getByRole('button',{name:'00분'}))
 fireEvent.click(screen.getByRole('checkbox',{name:'다음 날 종료'}))
 expect(save).not.toHaveBeenCalled()
 fireEvent.click(screen.getByRole('button',{name:'선택 완료'}))
 expect(save).toHaveBeenCalledWith('0',true)
 expect(close).toHaveBeenCalledOnce()
})
it('닫기는 선택을 저장하지 않으며 시작 시간에는 다음 날 옵션이 없다',()=>{
 const save=vi.fn(),close=vi.fn()
 render(<ValuePicker title="시작 시간" type="time" value="540" onSave={save} onClose={close} />)
 expect(screen.queryByRole('checkbox')).toBeNull()
 fireEvent.keyDown(screen.getByRole('spinbutton'),{key:'ArrowDown'})
 fireEvent.click(screen.getByRole('button',{name:'닫기'}))
 expect(save).not.toHaveBeenCalled();expect(close).toHaveBeenCalledOnce()
})

it('스크롤 도중 값이 변경되어도 오프셋을 강제로 되돌리지 않는다', () => {
 vi.useFakeTimers()
 try {
  render(<ValuePicker title="시작 시간" type="time" value="540" onSave={()=>{}} onClose={()=>{}} />)
  act(()=>vi.runOnlyPendingTimers())
  const wheel=screen.getByRole('spinbutton')
  wheel.scrollTop=430
  fireEvent.scroll(wheel)
  expect(wheel).toHaveAttribute('aria-valuenow','11')
  act(()=>vi.runOnlyPendingTimers())
  expect(wheel.scrollTop).toBe(430)
  fireEvent.keyDown(wheel,{key:'ArrowDown'})
  act(()=>vi.runOnlyPendingTimers())
  expect(wheel.scrollTop).toBe(480)
 } finally { vi.useRealTimers() }
})
