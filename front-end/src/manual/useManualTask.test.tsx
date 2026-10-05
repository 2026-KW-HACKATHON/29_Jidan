import {act,renderHook,waitFor} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {useManualTask} from './useManualTask'
it('응답 유실 재시도는 캡처한 요청과 같은 키를 유지한다',async()=>{
 const operation=vi.fn().mockRejectedValueOnce(Error('lost')).mockResolvedValueOnce(undefined),{result}=renderHook(useManualTask)
 await act(()=>result.current.run(operation));expect(result.current.error).not.toBe('');act(()=>result.current.retry());await waitFor(()=>expect(result.current.busy).toBe(false));expect(operation).toHaveBeenCalledTimes(2);expect(operation.mock.calls[0][1]).toBe(operation.mock.calls[1][1])
})
it('동시 요청을 차단하고 이탈하면 신호를 취소한다',async()=>{
 let finish!:()=>void;const operation=vi.fn((_signal:AbortSignal,_key:string)=>new Promise<void>(resolve=>finish=resolve));const {result,unmount}=renderHook(useManualTask)
 let first!:Promise<void>;act(()=>{first=result.current.run(operation);void result.current.run(operation)});expect(operation).toHaveBeenCalledOnce();unmount();expect(operation.mock.calls[0][0].aborted).toBe(true);finish();await first
})
