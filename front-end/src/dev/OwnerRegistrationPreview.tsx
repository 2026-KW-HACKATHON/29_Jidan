import { OwnerRegistration } from '../registration/owner/OwnerRegistration'
import { saveDraft } from '../registration/owner/draft'
import type { OwnerService } from '../registration/owner/service'

// Development-only fixture. Never writes authentication cookies or shares real drafts.
const previewStorage: Storage = {
  get length() { return sessionStorage.length },
  key: index => sessionStorage.key(index),
  getItem: key => sessionStorage.getItem(`preview.${key}`),
  setItem: (key, value) => sessionStorage.setItem(`preview.${key}`, value),
  removeItem: key => sessionStorage.removeItem(`preview.${key}`),
  clear: () => sessionStorage.removeItem('preview.jidan.owner-draft.v1'),
}
const receipt = { id: 'preview-receipt', ownerName: '김민수', storeName: '명랑핫도그 광운대점', status: 'PENDING' as const }
const step = new URLSearchParams(location.search).get('step')
saveDraft('owner-preview', { step: step === 'store' ? 2 : step === 'review' ? 3 : 1, requestKey: 'preview-request', draft: {
  name: '김민수', phone: '010-1234-5678', storeName: receipt.storeName, industry: '음식점', postcode: '01897', address: '서울 노원구 광운로 20', detailAddress: '1층', businessNumber: '220-81-62517', storePhone: '02-123-4567',
} }, previewStorage)
const service: OwnerService = {
  identity: async () => ({ draftScope: 'owner-preview', email: 'owner@example.com', ...(step === 'pending' ? { receipt } : {}) }),
  submit: async draft => {
    history.replaceState(null, '', '/__auth/owner?step=pending')
    return { ...receipt, ownerName: draft.name, storeName: draft.storeName }
  },
}
export default function OwnerRegistrationPreview() {
  return <OwnerRegistration service={service} storage={previewStorage} onBack={() => location.assign('/__auth/signup')} onExpired={() => location.assign('/__auth/signup')} />
}
