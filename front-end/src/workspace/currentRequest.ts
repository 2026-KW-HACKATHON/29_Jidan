import type {JobPosting,WorkRequest} from '../api/types.generated'
export function currentRequest(requests:WorkRequest[],job:JobPosting){
 const newest=[...requests].sort((a,b)=>Date.parse(b.requestedAt)-Date.parse(a.requestedAt))
 return newest.find(r=>r.status==='PENDING'||r.status==='ACCEPTED')??(job.status==='RECRUITING'&&newest[0]?.status==='EXPIRED'?newest[0]:undefined)
}
