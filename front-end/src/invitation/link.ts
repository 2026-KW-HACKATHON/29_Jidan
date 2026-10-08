const key='jidan.pending-invitation'
const lifetime=60*60*1000
export function captureInvitationLink(){if(window.location.pathname!=='/invitations/accept')return false;const token=new URLSearchParams(window.location.hash.slice(1)).get('token');try{if(token&&token.length<=512)sessionStorage.setItem(key,JSON.stringify({token,until:Date.now()+lifetime}));else sessionStorage.removeItem(key)}catch{/* A blocked session store cannot retain OAuth context. */}window.history.replaceState(null,'','/home');return true}
export function pendingInvitation(){try{const value=JSON.parse(sessionStorage.getItem(key)||'null');if(typeof value?.token==='string'&&value.until>Date.now())return value.token as string;sessionStorage.removeItem(key)}catch{/* Invalid or unavailable storage. */}return null}
export function clearInvitation(){try{sessionStorage.removeItem(key)}catch{/* Storage may be blocked. */}}
