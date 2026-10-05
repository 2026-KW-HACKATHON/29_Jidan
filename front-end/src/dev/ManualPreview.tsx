import {useMemo} from 'react'
import {ManualAuthoring} from '../manual/ManualAuthoring'
import {createManualPreviewService} from './manualPreviewService'
import {navigatePreview} from './navigation'
export default function ManualPreview(){const resume=new URLSearchParams(location.search).has('resume');const service=useMemo(()=>createManualPreviewService(resume),[resume]);return <ManualAuthoring service={service} onBack={()=>navigatePreview('/__preview')}/>}
