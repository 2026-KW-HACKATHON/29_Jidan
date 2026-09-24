import { RoleSelectionScreen } from '../auth/RoleSelectionScreen'

/** Visual-only development entry. Navigation still enters the real authentication guard. */
export default function RoleSelectionPreview() {
  return <RoleSelectionScreen onBack={() => window.location.assign('/')} onSelect={role => window.location.assign(`/signup/${role}`)} />
}
