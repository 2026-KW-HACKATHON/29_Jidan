import {expect,it} from 'vitest'
import {invitationFromApi} from './api'
import type {StoreInvitation} from '../api/types.generated'
it('초대 만료와 매장 접근 만료를 구분하고 재발송 날짜를 표시한다',()=>{const i={id:'i',storeId:'s',email:'member@example.com',status:'PENDING',createdAt:'2026-10-01T00:00:00Z',lastSentAt:'2026-10-07T15:00:00Z',expiresAt:'2026-10-14T15:00:00Z',accessExpiresAt:null,acceptedAt:null,acceptedBy:null,declinedAt:null,canceledAt:null} as StoreInvitation;const view=invitationFromApi(i,'매장');expect(view.sentAt).toBe('2026-10-08');expect(view.expiresAt).toBe('2026-10-15');expect(view.accessUntil).toBe('별도 종료 전까지');expect(invitationFromApi({...i,accessExpiresAt:'2026-11-01T00:00:00Z'},'매장').accessUntil).toBe('2026-11-01')})
