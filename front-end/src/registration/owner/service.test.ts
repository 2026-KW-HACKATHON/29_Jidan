import { expect, it } from 'vitest'
import { isOwnerReceipt, ownerService } from './service'
import { emptyDraft } from './model'
it('계약 미확정 상태에서 가입 성공을 만들거나 요청하지 않는다', async () => {
  const signal = new AbortController().signal
  await expect(ownerService.identity(signal)).rejects.toMatchObject({ code: 'unavailable' })
  await expect(ownerService.submit(emptyDraft, 'key', signal)).rejects.toMatchObject({ code: 'unavailable' })
})
it('서버의 승인 대기 접수 결과만 수락한다', () => {
  expect(isOwnerReceipt({ id: 'id', ownerName: '김민수', storeName: '매장', status: 'PENDING' })).toBe(true)
  for (const value of [null, {}, { id: 'id', ownerName: '김민수', storeName: '매장', status: 'VERIFIED' }, { id: '', ownerName: '김민수', storeName: '매장', status: 'PENDING' }]) expect(isOwnerReceipt(value)).toBe(false)
})
