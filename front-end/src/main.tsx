import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const root = createRoot(document.getElementById('root')!)

if (import.meta.env.DEV && window.location.pathname === '/__ui') {
  void import('./dev/ComponentPreview').then(({ default: ComponentPreview }) => {
    root.render(<StrictMode><ComponentPreview /></StrictMode>)
  })
} else if (import.meta.env.DEV && window.location.pathname === '/__auth/signup') {
  void import('./dev/RoleSelectionPreview').then(({ default: RoleSelectionPreview }) => {
    root.render(<StrictMode><RoleSelectionPreview /></StrictMode>)
  })
} else if (import.meta.env.DEV && window.location.pathname === '/__auth/owner') {
  void import('./dev/OwnerRegistrationPreview').then(({ default: OwnerRegistrationPreview }) => {
    root.render(<StrictMode><OwnerRegistrationPreview /></StrictMode>)
  })
} else if (import.meta.env.DEV && window.location.pathname === '/__auth/worker') {
  void import('./dev/WorkerRegistrationPreview').then(({ default: WorkerRegistrationPreview }) => {
    root.render(<StrictMode><WorkerRegistrationPreview /></StrictMode>)
  })
} else {
  root.render(<StrictMode><App /></StrictMode>)
}
