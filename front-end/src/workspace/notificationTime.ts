import {seoulDate} from '../home/api'
/** Relative labels follow the Figma list; calendar days use the service timezone. */
export function notificationTime(createdAt:string,now=Date.now()){
 const created=Date.parse(createdAt),elapsed=Math.max(0,now-created),day=seoulDate(createdAt),today=seoulDate(new Date(now).toISOString())
 if(elapsed<60000)return '방금 전'
 if(day===today)return elapsed<3600000?`${Math.floor(elapsed/60000)}분 전`:`${Math.floor(elapsed/3600000)}시간 전`
 if(day===seoulDate(new Date(now-86400000).toISOString()))return '어제'
 return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',...(day.slice(0,4)!==today.slice(0,4)?{year:'numeric' as const}:{}),month:'long',day:'numeric'}).format(new Date(created))
}
