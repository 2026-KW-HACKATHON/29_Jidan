/** Client wait limit only; it does not define a server timeout or API contract. */
export class DeadlineExceeded extends Error {
  constructor() { super('CLIENT_WAIT_EXCEEDED'); this.name = 'DeadlineExceeded' }
}
export async function withDeadline<T>(operation: (signal: AbortSignal) => Promise<T>, controller: AbortController, milliseconds = 10_000): Promise<T> {
  controller.signal.throwIfAborted()
  let rejectAbort!: (reason: unknown) => void
  const cancelled = new Promise<never>((_, reject) => { rejectAbort = reject })
  const abort = () => rejectAbort(controller.signal.reason)
  controller.signal.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(() => controller.abort(new DeadlineExceeded()), milliseconds)
  try { return await Promise.race([operation(controller.signal), cancelled]) }
  finally { clearTimeout(timer); controller.signal.removeEventListener('abort', abort) }
}
