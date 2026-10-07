import {act,fireEvent,render,screen} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {Resource} from './Resource'
it('실패 후 재시도하고 성공 데이터만 표시한다',async()=>{const load=vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValue('조회 완료');render(<Resource load={load} onBack={()=>{}}>{value=><p>{String(value)}</p>}</Resource>);await screen.findByRole('alert');fireEvent.click(screen.getByText('다시 시도'));await screen.findByText('조회 완료');expect(load).toHaveBeenCalledTimes(2)})
it('이탈하면 요청을 취소하고 늦은 결과를 버린다',async()=>{let finish!:(v:string)=>void;const load=vi.fn((_signal:AbortSignal)=>new Promise<string>(r=>{finish=r}));const {unmount}=render(<Resource load={load} onBack={()=>{}}>{v=><p>{v}</p>}</Resource>);unmount();expect(load.mock.calls[0][0].aborted).toBe(true);await act(async()=>finish('늦은 응답'));expect(screen.queryByText('늦은 응답')).not.toBeInTheDocument()})
