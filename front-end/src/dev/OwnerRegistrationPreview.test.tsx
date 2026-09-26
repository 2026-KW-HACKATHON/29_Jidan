import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
beforeEach(() => {
  sessionStorage.clear(); history.replaceState(null, '', '/__auth/owner'); vi.resetModules()
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
})
afterEach(() => { cleanup(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo') })
it('최초 진입에 샘플 성명·전화번호·매장 정보를 채우지 않는다', async () => {
  sessionStorage.setItem('preview.jidan.owner-draft.v1', JSON.stringify({ sample: 'old' }))
  const { default: Preview } = await import('./OwnerRegistrationPreview')
  render(<Preview />)
  const name = await screen.findByLabelText('점주 성명 *'), phone = screen.getByLabelText('연락처 *')
  expect(name).toHaveValue(''); expect(phone).toHaveValue('')
  fireEvent.change(name, { target: { value: '입력한 점주' } })
  fireEvent.change(phone, { target: { value: '01012345678' } })
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  for (const label of ['매장명 *', '우편번호', '매장 주소 *', '상세주소', '사업자 번호 *', '매장 연락처 *']) expect(screen.getByLabelText(label)).toHaveValue('')
  expect(screen.getByText('업종을 선택해 주세요')).toBeInTheDocument()
})
it('기본 진입 새로고침에서는 직접 입력한 값만 복원한다', async () => {
  const { default: Preview } = await import('./OwnerRegistrationPreview')
  const view = render(<Preview />)
  fireEvent.change(await screen.findByLabelText('점주 성명 *'), { target: { value: '직접 입력' } })
  view.unmount(); vi.resetModules()
  const { default: Reloaded } = await import('./OwnerRegistrationPreview')
  render(<Reloaded />)
  expect(await screen.findByLabelText('점주 성명 *')).toHaveValue('직접 입력')
})
