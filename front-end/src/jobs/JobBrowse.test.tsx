import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { mockDialog } from './dialogTestSupport'
mockDialog()
import { JobBrowse } from './JobBrowse'
import { jobToday,sampleJobs } from '../dev/jobFixtures'
function mount() { const onSelect=vi.fn(); render(<JobBrowse jobs={sampleJobs} today={jobToday} onSelect={onSelect} onHome={()=>{}} onProfile={()=>{}}/>);return onSelect }
it('검색 결과와 선택한 공고의 원본 데이터를 전달한다',()=>{
  const selected=mount()
  fireEvent.change(screen.getByLabelText('매장·업무 검색'),{target:{value:'야간'}})
  expect(screen.getByRole('status')).toHaveTextContent('공고 1개')
  fireEvent.click(screen.getByRole('button',{name:/야간 매장 관리 대타/}))
  expect(selected).toHaveBeenCalledWith(sampleJobs[2])
  expect(screen.getByText('9월 30일 / 22:00–다음 날 02:00')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('매장·업무 검색'),{target:{value:'없음'}})
  expect(screen.getByRole('status')).toHaveTextContent('공고 0개')
})
it('닫기는 선택을 폐기하고 적용할 때만 복합 필터를 반영한다',()=>{
  mount();fireEvent.click(screen.getByRole('button',{name:'필터'}))
  let dialog=within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getByLabelText('카페'))
  fireEvent.click(dialog.getByRole('button',{name:'닫기'}))
  expect(screen.getByRole('status')).toHaveTextContent('3개')
  fireEvent.click(screen.getByRole('button',{name:'필터'}))
  dialog=within(screen.getByRole('dialog'))
  expect(dialog.getByLabelText('카페')).not.toBeChecked()
  fireEvent.click(dialog.getByLabelText('카페'));fireEvent.click(dialog.getByLabelText('오늘'));fireEvent.click(dialog.getByLabelText('오전'))
  fireEvent.click(dialog.getByRole('button',{name:'공고 보기'}))
  expect(screen.getByRole('status')).toHaveTextContent('1개')
  fireEvent.click(screen.getByRole('button',{name:'필터'}));dialog=within(screen.getByRole('dialog'))
  expect(dialog.getByLabelText('카페')).toBeChecked()
  fireEvent.click(dialog.getByRole('button',{name:'초기화'}))
  expect(screen.getByRole('status')).toHaveTextContent('1개')
  fireEvent.click(dialog.getByRole('button',{name:'공고 보기'}))
  expect(screen.getByRole('status')).toHaveTextContent('3개')
})
it('초성 검색을 적용된 업종·날짜·시간 조건과 결합한다',()=>{
  mount();fireEvent.click(screen.getByRole('button',{name:'필터'}))
  const dialog=within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getByLabelText('카페'));fireEvent.click(dialog.getByLabelText('오늘'));fireEvent.click(dialog.getByLabelText('오전'))
  fireEvent.click(dialog.getByRole('button',{name:'공고 보기'}))
  fireEvent.change(screen.getByLabelText('매장·업무 검색'),{target:{value:'ㅋㅍㅈ'}})
  expect(screen.getByRole('status')).toHaveTextContent('1개')
  expect(screen.getByRole('button',{name:/주말 오픈 대타/})).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('매장·업무 검색'),{target:{value:'ㅅㅂㅇㄹㅂ'}})
  expect(screen.getByRole('status')).toHaveTextContent('0개')
})
