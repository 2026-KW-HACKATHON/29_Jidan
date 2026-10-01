/** Frontend state only. This is not an API response or authentication contract. */
export type Session = { displayName: string; accountType: 'OWNER' | 'WORKER' }
export type AuthState =
  | { kind: 'unavailable' }
  | { kind: 'guest' }
  | { kind: 'registration' }
  | { kind: 'authenticated'; session: Session }
export type AuthService = {
  read: (signal: AbortSignal) => Promise<AuthState>
  startGoogle: (signal: AbortSignal) => Promise<void>
}
/** Replace only after the Google authentication API is agreed. No speculative HTTP. */
export const authService: AuthService = {
  read: async () => ({ kind: 'unavailable' }),
  startGoogle: async () => { throw new Error('AUTH_NOT_CONFIGURED') },
}
