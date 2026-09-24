import { afterEach, expect, it, vi } from 'vitest'
import { searchAddress } from './postcode'
afterEach(() => { delete window.kakao; vi.restoreAllMocks() })
it('선택한 주소와 우편번호만 전달하고 종료 후 결과는 무시한다', async () => {
  let complete: (data: { zonecode: string; address: string; roadAddress: string; jibunAddress: string; userSelectedType: string }) => void = () => {}
  const embed = vi.fn()
  window.kakao = { Postcode: class { constructor(options: { oncomplete: typeof complete; onresize: () => void }) { complete = options.oncomplete; queueMicrotask(options.onresize) } embed = embed } }
  const controller = new AbortController(), onSelect = vi.fn(), container = document.createElement('div')
  await searchAddress(container, onSelect, controller.signal)
  expect(embed).toHaveBeenCalledWith(container)
  const result = { zonecode: '01897', address: '주소', roadAddress: '도로명 주소', jibunAddress: '지번 주소', userSelectedType: 'R' }
  complete({ ...result, zonecode: '' }); expect(onSelect).not.toHaveBeenCalled()
  complete(result); expect(onSelect).toHaveBeenLastCalledWith({ postcode: '01897', address: '도로명 주소' })
  complete({ ...result, userSelectedType: 'J' }); expect(onSelect).toHaveBeenLastCalledWith({ postcode: '01897', address: '지번 주소' })
  controller.abort(); complete(result); expect(onSelect).toHaveBeenCalledTimes(2)
})
it('닫은 뒤 로드된 검색창은 열지 않는다', async () => {
  const embed = vi.fn()
  window.kakao = { Postcode: class { embed = embed } }
  const controller = new AbortController(); controller.abort()
  await searchAddress(document.createElement('div'), vi.fn(), controller.signal)
  expect(embed).not.toHaveBeenCalled()
})

it('외부 검색 프레임이 로드되지 않으면 빈 화면을 제거하고 실패한다', async () => {
  vi.useFakeTimers()
  window.kakao = { Postcode: class { embed() {} } }
  const container = document.createElement('div')
  const result = searchAddress(container, vi.fn(), new AbortController().signal)
  const rejected = expect(result).rejects.toThrow('ADDRESS_FRAME_TIMEOUT')
  await vi.advanceTimersByTimeAsync(10000)
  await rejected
  expect(container.childElementCount).toBe(0)
  vi.useRealTimers()
})
