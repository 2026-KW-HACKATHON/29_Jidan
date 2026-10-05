import { ApiError, apiRequest, apiUrl } from '../api/client'
export type AccountType = 'OWNER' | 'WORKER'
export type StoreAccess = { storeId: string; storeName: string; approvalStatus: 'PENDING' | 'APPROVED'; permissions: string[] }
/** Home view derived from the server Session. Never infer authentication from storage/URL. */
export type Session = { displayName: string; accountType: AccountType; expiresAt?: string; nextAction?: 'WORKER_HOME' | 'OWNER_HOME' | 'OWNER_APPROVAL_PENDING'; stores?: StoreAccess[] }
export type RegistrationContext = { identity: { provider: 'GOOGLE'; email: string; emailVerified: true }; expiresAt: string; allowedRoles: AccountType[] }
export type ServerSession = { user: { id: string; name: string; role: AccountType; stores: StoreAccess[] }; expiresAt: string; nextAction: NonNullable<Session['nextAction']> }
export type AuthState =
  | { kind: 'unavailable' }
  | { kind: 'guest' }
  | { kind: 'registration'; context?: RegistrationContext }
  | { kind: 'authenticated'; session: Session }
export type AuthService = {
  read: (signal: AbortSignal) => Promise<AuthState>
  startGoogle: (signal: AbortSignal) => Promise<void>
  logout?: (signal: AbortSignal) => Promise<void>
}
export function parseSession(value: ServerSession): ServerSession {
  if (!value?.user || typeof value.user.id !== 'string' || !value.user.id || typeof value.user.name !== 'string' || !value.user.name.trim()
    || !['OWNER', 'WORKER'].includes(value.user.role) || !Number.isFinite(Date.parse(value.expiresAt)) || !Array.isArray(value.user.stores)
    || value.user.stores.some(store => !store || typeof store.storeId !== 'string' || !store.storeId || typeof store.storeName !== 'string' || !store.storeName.trim() || !['PENDING', 'APPROVED'].includes(store.approvalStatus) || !Array.isArray(store.permissions))
    || (value.user.role === 'WORKER' ? value.nextAction !== 'WORKER_HOME' || value.user.stores.length !== 0
      : value.nextAction !== (value.user.stores.some(store => store.approvalStatus === 'APPROVED') ? 'OWNER_HOME' : 'OWNER_APPROVAL_PENDING'))) throw new ApiError(0, 'INVALID_RESPONSE')
  return value
}
export async function registrationContext(signal: AbortSignal): Promise<RegistrationContext> {
  const context = await apiRequest<RegistrationContext>('/auth/registration', { signal })
  if (context?.identity?.provider !== 'GOOGLE' || context.identity.emailVerified !== true || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(context.identity.email)
    || !Number.isFinite(Date.parse(context.expiresAt)) || !Array.isArray(context.allowedRoles) || context.allowedRoles.length !== 2 || !context.allowedRoles.includes('OWNER') || !context.allowedRoles.includes('WORKER')) throw new ApiError(0, 'INVALID_RESPONSE')
  if (Date.parse(context.expiresAt) <= Date.now()) throw new ApiError(401, 'SESSION_EXPIRED')
  return context
}
export function createAuthService(redirect: (url: string) => void = url => window.location.assign(url)): AuthService {
  return {
    async read(signal) {
      try {
        const response = parseSession(await apiRequest<ServerSession>('/auth/session', { signal }))
        if (Date.parse(response.expiresAt) <= Date.now()) return { kind: 'guest' }
        return { kind: 'authenticated', session: { displayName: response.user.name, accountType: response.user.role, expiresAt: response.expiresAt, nextAction: response.nextAction, stores: response.user.stores } }
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401 && ['SESSION_EXPIRED', 'REGISTRATION_REQUIRED'].includes(error.code))) throw error
        try { return { kind: 'registration', context: await registrationContext(signal) } }
        catch (registrationError) {
          if (registrationError instanceof ApiError && registrationError.status === 401 && registrationError.code === 'SESSION_EXPIRED') return { kind: 'guest' }
          throw registrationError
        }
      }
    },
    async startGoogle(signal) { signal.throwIfAborted(); redirect(apiUrl('/auth/google')) },
    async logout(signal) { await apiRequest('/auth/logout', { method: 'POST', signal, allowExpiredCsrf: true }) },
  }
}
export const authService = createAuthService()
