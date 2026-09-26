import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { OwnerRegistration } from './OwnerRegistration'
import { emptyDraft } from './model'
import { saveDraft } from './draft'
import { OwnerFailure, type OwnerService } from './service'
const identity = { draftScope: 'ticket', email: 'owner@example.com', name: '김민수' }
const receipt = { id: 'receipt', ownerName: '김민수', storeName: '명랑핫도그 광운대점', status: 'PENDING' as const }
const valid = { ...emptyDraft, name: '김민수', phone: '01012345678', storeName: receipt.storeName, industry: '음식점' as const, postcode: '01897', address: '서울 노원구 광운로 20', businessNumber: '2208162517', storePhone: '021234567' }
const original = Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(() => {
  sessionStorage.clear()
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
})
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo')
  for (const key of ['showModal', 'close']) {
    if (original[key]) Object.defineProperty(HTMLDialogElement.prototype, key, original[key]); else Reflect.deleteProperty(HTMLDialogElement.prototype, key)
  }
})
function setup(service: OwnerService = { identity: vi.fn().mockResolvedValue(identity), submit: vi.fn().mockResolvedValue(receipt) }) {
  const onBack = vi.fn(), onExpired = vi.fn()
  const view = render(<OwnerRegistration service={service} onBack={onBack} onExpired={onExpired} />)
  return { service, onBack, onExpired, ...view }
}
function ready(step: 1 | 2 | 3 = 3) { saveDraft('ticket', { draft: valid, step, requestKey: 'same-key' }) }
it('필수값 오류를 안내하며 이전·수정 이동에서 값을 유지한다', async () => {
  setup()
  const phone = await screen.findByLabelText('연락처 *')
  expect(screen.getByLabelText('Google 이메일')).toHaveAttribute('readonly')
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  expect(phone).toHaveAttribute('aria-invalid', 'true')
  fireEvent.change(phone, { target: { value: '010-1234-5678' } })
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  expect(screen.getByLabelText('매장명 *')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('매장명 *'), { target: { value: '우리 매장' } })
  fireEvent.click(screen.getByRole('button', { name: '뒤로 가기' }))
  expect(screen.getByLabelText('연락처 *')).toHaveValue('010-1234-5678')
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  expect(screen.getByLabelText('매장명 *')).toHaveValue('우리 매장')
})
it('확인 화면 수정은 다른 단계 값을 유지하고 확인으로 복귀한다', async () => {
  ready(); setup()
  fireEvent.click(await screen.findByRole('button', { name: '점주 정보 수정' }))
  fireEvent.change(screen.getByLabelText('점주 성명 *'), { target: { value: '수정된 이름' } })
  fireEvent.click(screen.getByRole('button', { name: '입력 내용 확인' }))
  expect(screen.getByText('수정된 이름')).toBeInTheDocument()
  expect(screen.getByText(valid.storeName)).toBeInTheDocument()
})
it('중복 제출을 막고 확인된 접수 후 임시 입력을 지운다', async () => {
  ready()
  let resolve!: (value: typeof receipt) => void
  const submit = vi.fn(() => new Promise<typeof receipt>(done => { resolve = done }))
  setup({ identity: async () => identity, submit })
  const button = await screen.findByRole('button', { name: '매장 등록 신청' })
  fireEvent.click(button); fireEvent.click(button)
  expect(submit).toHaveBeenCalledTimes(1)
  expect(button).toBeDisabled()
  expect(screen.getByRole('button', { name: '점주 정보 수정' })).toBeDisabled()
  await act(async () => resolve(receipt))
  expect(screen.getByText('승인 대기중')).toBeInTheDocument()
  expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '매장 관리' }))
  expect(screen.getByRole('dialog', { name: '매장 승인 후 이용할 수 있어요' })).toBeInTheDocument()
})
it('실패 재시도는 동일 요청 키를 사용하고 입력을 보존한다', async () => {
  ready()
  const submit = vi.fn().mockRejectedValueOnce(new OwnerFailure('network')).mockResolvedValue(receipt)
  setup({ identity: async () => identity, submit })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  fireEvent.click(await screen.findByRole('button', { name: '다시 시도' }))
  await screen.findByText('승인 대기중')
  expect(submit.mock.calls.map(call => call[1])).toEqual(['same-key', 'same-key'])
})
it('서버 필드 오류는 해당 기본 정보 단계로 복귀한다', async () => {
  ready(); setup({ identity: async () => identity, submit: async () => { throw new OwnerFailure('validation', { phone: '연락처를 확인해 주세요.' }) } })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  fireEvent.click(await screen.findByRole('button', { name: '입력 수정' }))
  expect(screen.getByLabelText('연락처 *')).toHaveValue(valid.phone)
  expect(screen.getByText('연락처를 확인해 주세요.')).toBeInTheDocument()
})
it('만료 시 임시 입력을 폐기하고 재로그인으로 이동한다', async () => {
  ready(); const { onExpired } = setup({ identity: async () => identity, submit: async () => { throw new OwnerFailure('expired') } })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  fireEvent.click((await screen.findByRole('alertdialog')).querySelectorAll('button')[1])
  expect(onExpired).toHaveBeenCalledOnce()
  expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBeNull()
})
it('새로고침 시 서버 접수 결과가 있으면 가입 폼을 다시 열지 않는다', async () => {
  ready(); setup({ identity: async () => ({ ...identity, receipt }), submit: vi.fn() })
  await screen.findByText('승인 대기중')
  expect(screen.queryByRole('button', { name: '매장 등록 신청' })).not.toBeInTheDocument()
})
it('로드 실패 재시도는 뒤로 이동하지 않고 복구한다', async () => {
  const load = vi.fn().mockRejectedValueOnce(new OwnerFailure('unavailable')).mockResolvedValue(identity)
  const { onBack } = setup({ identity: load, submit: vi.fn() })
  const dialog = await screen.findByRole('alertdialog')
  fireEvent.click(dialog.querySelectorAll('button')[1])
  await screen.findByLabelText('점주 성명 *')
  expect(onBack).not.toHaveBeenCalled()
})
it('손상된 성공 응답을 승인 대기 완료로 표시하지 않는다', async () => {
  ready(); setup({ identity: async () => identity, submit: vi.fn().mockResolvedValue({ status: 'PENDING' }) })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  await screen.findByText('요청을 처리하지 못했어요')
  expect(screen.queryByText('승인 대기중')).not.toBeInTheDocument()
})
it('언마운트 시 진행 요청을 취소한다', async () => {
  ready(); const submit = vi.fn(() => new Promise<typeof receipt>(() => {}))
  const view = setup({ identity: async () => identity, submit })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  view.unmount()
  await waitFor(() => expect((submit.mock.calls[0] as unknown as [unknown, unknown, AbortSignal])[2].aborted).toBe(true))
})
it('중복 매장 거절 후 매장 수정으로 복귀하며 값을 유지한다', async () => {
  ready(); setup({ identity: async () => identity, submit: async () => { throw new OwnerFailure('duplicate') } })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  fireEvent.click(await screen.findByRole('button', { name: '입력 수정' }))
  expect(screen.getByLabelText('매장명 *')).toHaveValue(valid.storeName)
})
it('주소 선택 시 우편번호와 주소를 적용하고 이전 상세주소를 지운다', async () => {
  ready(2)
  render(<OwnerRegistration service={{ identity: async () => identity, submit: vi.fn() }} onBack={vi.fn()} onExpired={vi.fn()} addressSearch={async (_container, select) => { select({ postcode: '01890', address: '선택한 도로명 주소' }) }} />)
  fireEvent.click(await screen.findByRole('button', { name: '주소 검색' }))
  await waitFor(() => expect(screen.getByLabelText('매장 주소 *')).toHaveValue('선택한 도로명 주소'))
  expect(screen.getByLabelText('우편번호')).toHaveValue('01890')
  expect(screen.getByLabelText('상세주소')).toHaveValue('')
})
it('저장된 단계가 필수값을 우회하지 않는다', async () => {
  saveDraft('ticket', { draft: emptyDraft, step: 3, requestKey: 'key' })
  setup()
  expect(await screen.findByLabelText('점주 성명 *')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '매장 등록 신청' })).not.toBeInTheDocument()
})

it('전화번호 자동 형식과 blur 오류 및 입력 한도를 적용한다', async () => {
  setup()
  const phone = await screen.findByLabelText('연락처 *')
  fireEvent.change(phone, { target: { value: '0101234567899999' } })
  expect(phone).toHaveValue('010-1234-5678')
  fireEvent.change(phone, { target: { value: '010123' } }); fireEvent.blur(phone)
  expect(phone).toHaveAttribute('aria-invalid', 'true')
  fireEvent.change(phone, { target: { value: '01012345678' } }); fireEvent.blur(phone)
  expect(phone).not.toHaveAttribute('aria-invalid', 'true')
  fireEvent.click(screen.getByRole('button', { name: '다음' }))
  const business = screen.getByLabelText('사업자 번호 *')
  fireEvent.change(business, { target: { value: '22081625171234' } })
  expect(business).toHaveValue('220-81-62517')
  const store = screen.getByLabelText('매장 연락처 *')
  fireEvent.change(store, { target: { value: '02123456789999' } })
  expect(store).toHaveValue('02-1234-5678')
  fireEvent.change(business, { target: { value: '0000000000' } }); fireEvent.blur(business)
  expect(business).toHaveAttribute('aria-invalid', 'true')
})


it('만료 오류를 닫아도 입력을 폐기하고 재로그인 전 저장과 제출을 막는다', async () => {
  ready()
  const submit = vi.fn().mockRejectedValue(new OwnerFailure('expired'))
  const { onExpired } = setup({ identity: async () => identity, submit })
  fireEvent.click(await screen.findByRole('button', { name: '매장 등록 신청' }))
  await screen.findByRole('alertdialog')
  fireEvent.click(screen.getByRole('button', { name: '닫기' }))
  expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBeNull()
  expect(screen.queryByText(valid.name)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '점주 정보 수정' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '매장 등록 신청' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '다시 로그인' }))
  expect(onExpired).toHaveBeenCalledOnce()
  expect(submit).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBeNull()
})

it('최초 가입 정보 조회가 만료되면 오류를 닫아도 재로그인으로만 진행한다', async () => {
  ready()
  const load = vi.fn().mockRejectedValue(new OwnerFailure('expired'))
  const { onExpired } = setup({ identity: load, submit: vi.fn() })
  fireEvent.click((await screen.findByRole('alertdialog')).querySelectorAll('button')[0])
  expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '다시 로그인' }))
  expect(onExpired).toHaveBeenCalledOnce()
  expect(load).toHaveBeenCalledTimes(1)
})
