import { fireEvent,render,screen } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import { MemberNavigation } from './MemberNavigation'
it('선택된 메뉴만 활성화하고 각 이동 콜백을 사용한다',()=>{
  const home=vi.fn(),jobs=vi.fn(),manual=vi.fn(),profile=vi.fn()
  render(<MemberNavigation active="jobs" onHome={home} onJobs={jobs} onManual={manual} onProfile={profile}/>)
  expect(screen.getByRole('button',{name:'공고 찾기'})).toHaveAttribute('aria-current','page')
  expect(screen.getByRole('button',{name:'홈'})).not.toHaveAttribute('aria-current')
  for (const label of ['홈','공고 찾기','매뉴얼','프로필']) fireEvent.click(screen.getByRole('button',{name:label}))
  for (const callback of [home,jobs,manual,profile]) expect(callback).toHaveBeenCalledOnce()
})
