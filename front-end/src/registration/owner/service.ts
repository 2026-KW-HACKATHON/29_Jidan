import type { OwnerDraft, OwnerErrors } from './model'

/** Server-owned identity. Never populate this from query parameters or draft storage. */
export type OwnerIdentity = { draftScope: string; email: string; name?: string }
export type OwnerReceipt = { id: string; ownerName: string; storeName: string; status: 'PENDING' }
export type OwnerFailureCode = 'unavailable' | 'expired' | 'duplicate' | 'validation' | 'network'
export class OwnerFailure extends Error {
  code: OwnerFailureCode
  fields: OwnerErrors
  constructor(code: OwnerFailureCode, fields: OwnerErrors = {}) { super(code); this.code = code; this.fields = fields }
}
export type OwnerService = {
  identity: (signal: AbortSignal) => Promise<OwnerIdentity>
  submit: (draft: OwnerDraft, idempotencyKey: string, signal: AbortSignal) => Promise<OwnerReceipt>
}
/**
 * Figma's owner profile/store payload is not supported by the current API draft.
 * No speculative POST: the backend adapter must replace this fail-closed boundary.
 * submit must validate the ticket/CSRF and save the full request atomically and idempotently.
 */
export const ownerService: OwnerService = {
  identity: async () => { throw new OwnerFailure('unavailable') },
  submit: async () => { throw new OwnerFailure('unavailable') },
}
export function isOwnerReceipt(value: unknown): value is OwnerReceipt {
  if (!value || typeof value !== 'object') return false
  const receipt = value as Partial<OwnerReceipt>
  return receipt.status === 'PENDING' && typeof receipt.id === 'string' && receipt.id.length > 0
    && typeof receipt.ownerName === 'string' && receipt.ownerName.trim().length > 0
    && typeof receipt.storeName === 'string' && receipt.storeName.trim().length > 0
}
