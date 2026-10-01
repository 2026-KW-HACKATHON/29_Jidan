import { navigatePreview } from './navigation'
import { RoleSelectionScreen } from '../auth/RoleSelectionScreen'

/** Visual-only development entry. Routes stay inside isolated previews. */
export default function RoleSelectionPreview() {
  return <RoleSelectionScreen onBack={() => navigatePreview('/__preview/login')} onSelect={role => navigatePreview(`/__auth/${role}`)} />
}
