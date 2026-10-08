import {useMemo} from 'react'
import {ManualAuthoring} from '../manual/ManualAuthoring'
import {createManualPreviewService} from './manualPreviewService'
import {navigatePreview} from './navigation'
export default function ManualPreview(){const scenario=new URLSearchParams(location.search).get('case')??'';const resume=new URLSearchParams(location.search).has('resume');const service=useMemo(()=>createManualPreviewService(resume,scenario),[resume,scenario]);return <ManualAuthoring service={service} onBack={()=>navigatePreview('/__preview')}/>}
