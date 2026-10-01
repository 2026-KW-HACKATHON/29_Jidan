import {act,fireEvent,render,screen,waitFor,within} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import JobsPreview from './JobsPreview'
import Preview from './PreviewApp'
import { mockDialog } from '../jobs/dialogTestSupport'
mockDialog()
it('상세 복귀와 브라우저 이력에서 검색어·필터·목록 노드를 유지한다',async()=>{
  history.replaceState(null,'','/__jobs');vi.stubGlobal('fetch',vi.fn())
  render(<JobsPreview/>)
  const input=screen.getByLabelText('매장·업무 검색'),body=input.closest('main')!
  fireEvent.change(input,{target:{value:'카페'}});body.scrollTop=90
  fireEvent.click(screen.getByRole('button',{name:/주말 오픈 대타/}))
  expect(screen.getByRole('heading',{name:'공고 상세'})).toBeVisible()
  expect(input).not.toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}))
  expect(input).toBeVisible();expect(input).toHaveValue('카페');expect(body.scrollTop).toBe(90)
  await act(async()=>history.back())
  await waitFor(()=>expect(screen.getByRole('heading',{name:'공고 상세'})).toBeVisible())
  await act(async()=>history.forward())
  await waitFor(()=>expect(input).toBeVisible())
  expect(fetch).not.toHaveBeenCalled()
})
it('대시보드의 같은 공고 경로 이동은 상세에서 목록으로 검색 상태를 보존한다',async()=>{
  history.replaceState(null,'','/__jobs')
  await act(async()=>{render(<Preview/>)})
  const input=await screen.findByLabelText('매장·업무 검색');fireEvent.change(input,{target:{value:'카페'}})
  fireEvent.click(screen.getByRole('button',{name:/주말 오픈 대타/}))
  await screen.findByRole('heading',{name:'공고 상세'})
  fireEvent.click(within(screen.getByRole('navigation',{name:'미리보기 화면 목록'})).getByRole('link',{name:'공고 탐색'}))
  await waitFor(()=>expect(input).toBeVisible());expect(input).toHaveValue('카페')
})
