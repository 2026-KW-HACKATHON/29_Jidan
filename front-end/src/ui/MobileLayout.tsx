import type { ReactNode } from 'react'
import './tokens.css'
import './MobileLayout.css'

/** A single mobile viewport; header/footer stay inside the 390px frame. */
export function MobileLayout({ children, header, footer }: {
  children: ReactNode
  header?: ReactNode
  footer?: ReactNode
}) {
  return <div className="ds-ui ds-mobile">
    {header && <header className="ds-mobile-header">{header}</header>}
    <main className="ds-mobile-body">{children}</main>
    {footer && <footer className="ds-mobile-footer">{footer}</footer>}
  </div>
}
