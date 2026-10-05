import { expect, it } from 'vitest'
import { isWorkerReceipt, workerService } from './service'
import { emptyWorker } from './model'
it('실제 어댑터가 없으면 가입 완료로 처리하지 않는다',async()=>{
  await expect(workerService.identity(new AbortController().signal)).rejects.toMatchObject({code:'unavailable'})
  await expect(workerService.submit(emptyWorker,'key',new AbortController().signal)).rejects.toMatchObject({code:'unavailable'})
})
it('등록 완료 응답의 id와 상태를 확인한다',()=>{
  for(const value of [null,{}, {id:'',status:'COMPLETE'},{id:'a',status:'PENDING'}])expect(isWorkerReceipt(value)).toBe(false)
  expect(isWorkerReceipt({id:'a',status:'COMPLETE'})).toBe(true)
})
