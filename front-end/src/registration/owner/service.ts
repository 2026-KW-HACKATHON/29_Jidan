import { ApiError, apiRequest } from '../../api/client'
import { parseSession, registrationContext, type ServerSession } from '../../auth/session'
import type { OwnerDraft, OwnerErrors } from './model'

/** Server-owned identity. Never populate this from query parameters or draft storage. */
export type OwnerIdentity = { draftScope: string; email: string; name?: string; receipt?: OwnerReceipt }
export type OwnerReceipt = { id: string; ownerName: string; storeName: string; status: 'PENDING' }
export type OwnerFailureCode = 'unavailable' | 'expired' | 'duplicate' | 'validation' | 'network'
export class OwnerFailure extends Error {
  code: OwnerFailureCode
  fields: OwnerErrors
  constructor(code: OwnerFailureCode, fields: OwnerErrors = {}, message = code as string) { super(message); this.code = code; this.fields = fields }
}
export type OwnerService = {
  identity: (signal: AbortSignal) => Promise<OwnerIdentity>
  submit: (draft: OwnerDraft, idempotencyKey: string, signal: AbortSignal) => Promise<OwnerReceipt>
}
const industryCodes = { '음식점': 'RESTAURANT', '카페': 'CAFE', '편의점': 'CONVENIENCE_STORE', '기타': 'OTHER', '': '' }
const fields: Record<string, keyof OwnerDraft> = { name: 'name', phoneNumber: 'phone', 'store.name': 'storeName', 'store.industry': 'industry', 'store.postalCode': 'address', 'store.address': 'address', 'store.detailAddress': 'detailAddress', 'store.businessRegistrationNumber': 'businessNumber', 'store.phoneNumber': 'storePhone' }
function failure(error: unknown): never {
  if (!(error instanceof ApiError)) throw error
  const code: OwnerFailureCode = error.status === 401 || error.code === 'ALREADY_REGISTERED' ? 'expired' : error.code === 'STORE_ALREADY_REGISTERED' ? 'duplicate' : error.status === 422 || error.code === 'STORE_OUTSIDE_SERVICE_AREA' ? 'validation' : 'network'
  const mapped: OwnerErrors = {}
  for (const field of error.fieldErrors) if (fields[field.field]) mapped[fields[field.field]] = field.message
  if (error.code === 'STORE_OUTSIDE_SERVICE_AREA') mapped.address = error.message
  throw new OwnerFailure(code, mapped, error.message)
}
export const ownerService: OwnerService = {
  async identity(signal) {
    try {
      const context = await registrationContext(signal)
      return { email: context.identity.email, draftScope: context.identity.email }
    } catch (error) { return failure(error) }
  },
  async submit(draft, idempotencyKey, signal) {
    try {
      const response = parseSession(await apiRequest<ServerSession>('/auth/registrations/owners', {
        method: 'POST', signal, idempotencyKey,
        body: { name: draft.name.trim(), phoneNumber: draft.phone.replace(/[\s-]/g, ''), store: {
          name: draft.storeName.trim(), industry: industryCodes[draft.industry], postalCode: draft.postcode,
          address: draft.address.trim(), detailAddress: draft.detailAddress.trim(),
          businessRegistrationNumber: draft.businessNumber.replace(/[\s-]/g, ''), phoneNumber: draft.storePhone.replace(/[\s-]/g, ''),
        } },
      }))
      const store = response.user.stores[0]
      if (response.user.role !== 'OWNER' || response.nextAction !== 'OWNER_APPROVAL_PENDING' || !store || store.approvalStatus !== 'PENDING') throw new ApiError(0, 'INVALID_RESPONSE')
      return { id: store.storeId, ownerName: response.user.name, storeName: store.storeName, status: 'PENDING' }
    } catch (error) { return failure(error) }
  },
}
export function isOwnerReceipt(value: unknown): value is OwnerReceipt {
  if (!value || typeof value !== 'object') return false
  const receipt = value as Partial<OwnerReceipt>
  return receipt.status === 'PENDING' && typeof receipt.id === 'string' && receipt.id.length > 0
    && typeof receipt.ownerName === 'string' && receipt.ownerName.trim().length > 0
    && typeof receipt.storeName === 'string' && receipt.storeName.trim().length > 0
}
