import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const root = createRoot(document.getElementById('root')!)

if (import.meta.env.DEV && window.location.pathname === '/__ui') {
  void import('./dev/ComponentPreview').then(({ default: ComponentPreview }) => {
    root.render(<StrictMode><ComponentPreview /></StrictMode>)
  })
} else {
  root.render(<StrictMode><App /></StrictMode>)
}
