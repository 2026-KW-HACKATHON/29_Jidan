import {expect,it} from 'vitest'
import {createApplicationPreviewService} from './applicationPreviewService'
import {sampleJobs} from './jobFixtures'
it('샘플 실패 후 재시도·중복 지원 방지·철회 후 재지원 상태를 유지한다',async()=>{
  const service=createApplicationPreviewService([],true),signal=new AbortController().signal
  await expect(service.submit(sampleJobs[0],'소개',signal)).rejects.toThrow('MOCK_SUBMIT_FAILURE')
  const app=await service.submit(sampleJobs[0],'소개',signal)
  expect(await service.submit(sampleJobs[0],'다른 소개',signal)).toBe(app)
  await expect(service.withdraw(app.id,signal)).rejects.toThrow('MOCK_WITHDRAW_FAILURE')
  await service.withdraw(app.id,signal)
  expect((await service.submit(sampleJobs[0],'새 소개',signal)).id).not.toBe(app.id)
})
it('취소한 샘플 요청은 변경 전에 중단한다',async()=>{
  const service=createApplicationPreviewService(),controller=new AbortController()
  const result=service.submit(sampleJobs[0],'소개',controller.signal);controller.abort()
  await expect(result).rejects.toBeDefined()
  expect((await service.submit(sampleJobs[0],'소개',new AbortController().signal)).id).toBe('preview-application-1')
})
