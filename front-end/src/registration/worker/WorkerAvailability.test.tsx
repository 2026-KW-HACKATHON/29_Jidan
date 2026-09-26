import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { WorkerAvailability } from './WorkerAvailability'
import { slots } from './model'
it('드래그 범위를 30분 단위로 확정하고 취소된 드래그는 저장하지 않는다',()=>{
 const change=vi.fn(), point=Object.getOwnPropertyDescriptor(document,'elementFromPoint')
 const view=render(<WorkerAvailability values={[]} change={change} edit={vi.fn()} />)
 const first=screen.getByRole('button',{name:'월 06:00–06:30'}),last=screen.getByRole('button',{name:'월 07:00–07:30'})
 Object.defineProperty(first,'setPointerCapture',{value:vi.fn()})
 Object.defineProperty(document,'elementFromPoint',{configurable:true,value:()=>last})
 try {
   fireEvent(first,new MouseEvent('pointerdown',{bubbles:true,button:0}))
   fireEvent(last,new MouseEvent('pointermove',{bubbles:true,clientX:1,clientY:1}))
   expect(last).toHaveAttribute('aria-pressed','true')
   expect(change).not.toHaveBeenCalled()
   fireEvent(last,new MouseEvent('pointerup',{bubbles:true}))
   expect(change).toHaveBeenCalledOnce()
   expect(change.mock.calls[0][0].flatMap(slots)).toEqual([12,13,14])
   fireEvent(first,new MouseEvent('pointerdown',{bubbles:true,button:0}))
   fireEvent(first,new MouseEvent('pointercancel',{bubbles:true}))
   expect(change).toHaveBeenCalledOnce()
 } finally {view.unmount();if(point)Object.defineProperty(document,'elementFromPoint',point);else Reflect.deleteProperty(document,'elementFromPoint')}
})
