import { navigatePreview } from './navigation'
import { useState } from 'react'
import { WorkerProfile } from '../profile/WorkerProfile'
import { createProfilePreviewService,sampleProfile } from './profilePreviewService'
export default function WorkerProfilePreview(){const [service]=useState(()=>createProfilePreviewService(sampleProfile,new URLSearchParams(location.search).has('fail')));return <WorkerProfile initialProfile={sampleProfile} service={service} onBack={()=>navigatePreview('/__home/worker')}/>}
