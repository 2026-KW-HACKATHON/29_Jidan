import { RoleSelectionScreen } from '../auth/RoleSelectionScreen'

/** Visual-only development entry. Routes stay inside isolated previews. */
export default function RoleSelectionPreview() {
  return <RoleSelectionScreen onBack={() => window.location.assign('/__preview/login')} onSelect={role => window.location.assign(`/__auth/${role}`)} />
}
