import { fireEvent,render,screen } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import { JobDetail } from './JobDetail'
import { sampleJobs } from '../dev/jobFixtures'
it('선택 공고의 데이터로 상세·다음 날 종료·추정 급여를 표시한다',()=>{
  const back=vi.fn(),apply=vi.fn()
  render(<JobDetail job={sampleJobs[2]} onBack={back} onApply={apply}/>)
  expect(screen.getByRole('heading',{name:'야간 매장 관리 대타'})).toBeInTheDocument()
  expect(screen.getByText('22:00–다음 날 02:00')).toBeInTheDocument()
  expect(screen.getByText('4시간 기준 56,000원')).toBeInTheDocument()
  expect(screen.getByText(sampleJobs[2].address)).toBeInTheDocument()
  expect(screen.getByText(sampleJobs[2].tasks[0])).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'지원하기'}));expect(apply).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(back).toHaveBeenCalledOnce()
})
