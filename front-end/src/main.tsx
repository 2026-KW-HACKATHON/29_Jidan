import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const root = createRoot(document.getElementById('root')!)

// Vite folds this build-time boundary; production has no preview imports.
if ((import.meta.env.DEV || import.meta.env.VITE_ENABLE_PREVIEW === 'true') && window.location.pathname.startsWith('/__')) {
  void import('./dev/PreviewApp').then(({default: PreviewApp})=>root.render(<StrictMode><PreviewApp /></StrictMode>))
} else {
  root.render(<StrictMode><App /></StrictMode>)
}
