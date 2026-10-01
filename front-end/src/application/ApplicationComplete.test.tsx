import {act,fireEvent,render,screen,within} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {ApplicationComplete} from './ApplicationComplete'
import {sampleJobs} from '../dev/jobFixtures'
import {mockDialog} from '../jobs/dialogTestSupport'
import type {ApplicationService} from './model'
mockDialog()
const application={id:'1',job:sampleJobs[0],introduction:'지원해요'}
it('철회 실패 시 승인을 유지하고 재시도 성공 시에만 복귀한다',async()=>{
  const withdraw=vi.fn<ApplicationService['withdraw']>().mockRejectedValueOnce(Error('MOCK')).mockResolvedValue(undefined),success=vi.fn(),home=vi.fn()
  render(<ApplicationComplete application={application} service={{submit:vi.fn(),withdraw}} onHome={home} onBack={()=>{}} onWithdrawn={success}/>)
  expect(screen.getByText('2026. 09. 26')).toBeInTheDocument()
  expect(screen.getByText('9:00-14:00')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'신청 철회하기'}))
  let modal=within(screen.getByRole('alertdialog'))
  fireEvent.click(modal.getByRole('button',{name:'지원 철회'}))
  await screen.findByRole('alert')
  expect(success).not.toHaveBeenCalled();expect(screen.getByText('승인 대기 중')).toBeInTheDocument()
  modal=within(screen.getByRole('alertdialog'));fireEvent.click(modal.getByRole('button',{name:'지원 철회'}))
  await act(async()=>{})
  expect(success).toHaveBeenCalledOnce();expect(withdraw).toHaveBeenCalledTimes(2)
})
it('닫기와 해제 이후 지연된 철회 결과는 화면을 이동하지 않는다',async()=>{
  let resolve!:()=>void
  const withdraw=vi.fn<ApplicationService['withdraw']>(()=>new Promise<void>(r=>{resolve=r})),success=vi.fn()
  render(<ApplicationComplete application={application} service={{submit:vi.fn(),withdraw}} onHome={()=>{}} onBack={()=>{}} onWithdrawn={success}/>)
  fireEvent.click(screen.getByRole('button',{name:'신청 철회하기'}))
  fireEvent.click(screen.getByRole('button',{name:'지원 철회'}));fireEvent.click(screen.getByRole('button',{name:'지원 철회'}))
  expect(withdraw).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button',{name:'돌아가기'}))
  expect(withdraw.mock.calls[0][1].aborted).toBe(true)
  await act(async()=>resolve())
  expect(success).not.toHaveBeenCalled();expect(screen.getByText('승인 대기 중')).toBeInTheDocument()
})
