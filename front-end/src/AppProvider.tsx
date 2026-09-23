import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { AppContext } from './app-context'
import { createDemoData, STORAGE_KEY } from './model'
import type { AppData } from './model'

function loadData(): AppData {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (saved && ['owner', 'member', null].includes(saved.role) && saved.store && saved.profile && ['workers', 'invites', 'manuals', 'jobs', 'savedJobs'].every(key => Array.isArray(saved[key])) && Array.isArray(saved.profile.careers) && Array.isArray(saved.profile.availability)) return saved
  } catch { /* Fall back to a fresh demo if storage is unavailable or invalid. */ }
  return createDemoData()
}
export function AppProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AppData>(loadData)
  const [toast, setToast] = useState('')
  const [now, setNow] = useState(Date.now)
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  useEffect(() => { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)) } catch { /* A full or blocked store must not interrupt the prototype. */ } }, [data])
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 4200); return () => clearTimeout(timer) }, [toast])
  const update = (updater: (previous: AppData) => AppData) => setData(previous => {
    const next = updater(previous)
    return next.invites !== previous.invites || next.jobs !== previous.jobs ? { ...next, readNotifications: false } : next
  })
  const go = (path: string) => { window.location.hash = path }
  return <AppContext.Provider value={{ data, now, update, go, notify: setToast, reset: () => { setData(createDemoData()); go('/'); setToast('샘플 데이터를 처음 상태로 되돌렸어요.') } }}>{children}{toast && <div role="status" className="toast">{toast}</div>}</AppContext.Provider>
}
