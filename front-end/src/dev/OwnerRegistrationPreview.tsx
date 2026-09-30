import { OwnerRegistration } from '../registration/owner/OwnerRegistration'
import { restoreDraft, saveDraft } from '../registration/owner/draft'
import { emptyDraft } from '../registration/owner/model'
import type { OwnerService } from '../registration/owner/service'

// Development-only fixture. Never writes authentication cookies or shares real drafts.
const previewStorage: Storage = {
  get length() { return sessionStorage.length },
  key: index => sessionStorage.key(index),
  getItem: key => sessionStorage.getItem(`preview.v2.${key}`),
  setItem: (key, value) => sessionStorage.setItem(`preview.v2.${key}`, value),
  removeItem: key => sessionStorage.removeItem(`preview.v2.${key}`),
  clear: () => sessionStorage.removeItem('preview.v2.jidan.owner-draft.v1'),
}
const receipt = { id: 'preview-receipt', ownerName: '김민수', storeName: '명랑핫도그 광운대점', status: 'PENDING' as const }
const step = new URLSearchParams(location.search).get('step')
// Explicit review/pending URLs are visual fixtures; the normal signup starts empty.
if (step || !restoreDraft('owner-preview', previewStorage)) saveDraft('owner-preview', {
  step: step === 'store' ? 2 : step === 'review' ? 3 : 1,
  requestKey: crypto.randomUUID(),
  draft: step === 'review' ? {
    name: '김민수', phone: '010-1234-5678', storeName: receipt.storeName, industry: '음식점', postcode: '01897', address: '서울 노원구 광운로 20', detailAddress: '1층', businessNumber: '220-81-62517', storePhone: '02-123-4567',
  } : step === 'store' ? { ...emptyDraft, name: '김민수', phone: '010-1234-5678' } : { ...emptyDraft },
}, previewStorage)
let calls=0
const service: OwnerService = {
  identity: async () => ({ draftScope: 'owner-preview', email: 'owner@example.com', ...(step === 'pending' ? { receipt } : {}) }),
  submit: async draft => {
    if (new URLSearchParams(location.search).has('fail') && calls++ === 0) throw Error('MOCK_FAILURE')
    history.replaceState(null, '', '/__auth/owner?step=pending')
    return { ...receipt, ownerName: draft.name, storeName: draft.storeName }
  },
}
export default function OwnerRegistrationPreview() {
  return <OwnerRegistration service={service} storage={previewStorage} onBack={() => location.assign('/__auth/signup')} onExpired={() => location.assign('/__auth/signup')} />
}
