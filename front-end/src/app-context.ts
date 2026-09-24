import { createContext, useContext } from 'react'
import type { AppData } from './model'

export type AppContextValue = { data: AppData; now: number; update: (updater: (previous: AppData) => AppData) => void; go: (path: string) => void; notify: (message: string) => void; reset: () => void }
export const AppContext = createContext<AppContextValue | null>(null)
export function useApp() { const context = useContext(AppContext); if (!context) throw new Error('AppProvider is required'); return context }
