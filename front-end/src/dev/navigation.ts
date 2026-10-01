import { useEffect, useState, type MouseEvent } from 'react'

const snapshot = () => window.location.pathname + window.location.search

/** Preview-only navigation; preserves the document and its isolated mock services. */
export function navigatePreview(url: string) {
  if (snapshot() === url) return
  window.history.pushState(null, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function usePreviewLocation() {
  const [url, setUrl] = useState(snapshot)
  useEffect(() => {
    const changed = () => setUrl(snapshot())
    window.addEventListener('popstate', changed)
    return () => window.removeEventListener('popstate', changed)
  }, [])
  return url
}

/** Keep native link semantics for new tabs, downloads and external destinations. */
export function handlePreviewLink(event: MouseEvent<HTMLElement>) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null
  if (!(anchor instanceof HTMLAnchorElement) || anchor.hasAttribute('download') || (anchor.target && anchor.target !== '_self')) return
  const url = new URL(anchor.href)
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/__') || url.hash) return
  event.preventDefault()
  navigatePreview(url.pathname + url.search)
}
