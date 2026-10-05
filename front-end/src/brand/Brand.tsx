import loginLogo from './assets/logo-login.svg'
import appLogo from './assets/logo-appbar.svg'
import character from './assets/character.png'
import './Brand.css'
/** Original Figma exports; the character remains decorative beside the named logo. */
export function Brand({ variant = 'appbar' }: { variant?: 'login' | 'appbar' }) {
  return <span className={`jidan-brand jidan-brand-${variant}`}>
    {variant === 'login' && <span className="jidan-brand-character"><img src={character} alt="" /></span>}
    <img src={variant === 'login' ? loginLogo : appLogo} alt="지단" />
  </span>
}
