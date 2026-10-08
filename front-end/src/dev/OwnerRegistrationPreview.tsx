import { useState } from 'react'
import { navigatePreview } from './navigation'
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
function createPreviewService(): OwnerService {
  const params = new URLSearchParams(location.search)
  const step = params.get('step')
  let initialized = false, calls = 0
  return {
    identity: async () => {
      if (!initialized) {
        initialized = true
        // Seed only this entry, including repeated visits after the lazy module is cached.
        if (step || !restoreDraft('owner-preview', previewStorage)) saveDraft('owner-preview', {
          step: step === 'store' ? 2 : step === 'review' ? 3 : 1,
          requestKey: crypto.randomUUID(),
          draft: step === 'review' ? {
            name: '김민수', phone: '010-1234-5678', storeName: receipt.storeName, industry: '음식점', postcode: '01897', address: '서울 노원구 광운로 20', detailAddress: '1층', businessNumber: '220-81-62517', storePhone: '02-123-4567',
          } : step === 'store' ? { ...emptyDraft, name: '김민수', phone: '010-1234-5678' } : { ...emptyDraft },
        }, previewStorage)
      }
      return { draftScope: 'owner-preview', email: 'owner@example.com', ...(step === 'pending' ? { receipt } : {}) }
    },
    submit: async draft => {
      if (params.has('fail') && calls++ === 0) throw Error('MOCK_FAILURE')
      history.replaceState(null, '', '/__auth/owner?step=pending')
      return { ...receipt, ownerName: draft.name, storeName: draft.storeName }
    },
  }
}
export default function OwnerRegistrationPreview() {
  const [service]=useState(createPreviewService)
  return <OwnerRegistration service={service} storage={previewStorage} onBack={() => navigatePreview('/__preview/signup')} onExpired={() => navigatePreview('/__preview/signup')} />
}
