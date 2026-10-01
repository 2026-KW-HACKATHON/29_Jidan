import type { Job } from '../jobs/model'
export const jobToday = new Date(2026,8,26)
export const sampleJobs: Job[] = [
  {id:'cafe',industry:'카페',title:'주말 오픈 대타',storeName:'컴포즈커피 광운대점',address:'서울 노원구 광운로 20 · 월계1동',date:'2026-09-26',start:'09:00',end:'14:00',nextDay:false,hourlyPay:12000,headcount:1,applicants:3,publishedAt:'2026-09-25T12:00:00Z',experience:'신입 가능',tasks:['음료 제조 보조','주문 접수 및 고객 응대','매장 오픈 준비와 정리']},
  {id:'restaurant',industry:'식당',title:'평일 저녁 홀 대타',storeName:'국수천왕 광운대본점',address:'서울 노원구 광운로 · 월계1동',date:'2026-09-29',start:'18:00',end:'22:00',nextDay:false,hourlyPay:13000,headcount:1,applicants:1,publishedAt:'2026-09-24T12:00:00Z',experience:'신입 가능',tasks:['홀 정리','고객 응대']},
  {id:'night',industry:'편의점',title:'야간 매장 관리 대타',storeName:'세븐일레븐 광운스퀘어점',address:'서울 노원구 광운로 · 월계1동',date:'2026-09-30',start:'22:00',end:'02:00',nextDay:true,hourlyPay:14000,headcount:1,applicants:0,publishedAt:'2026-09-23T12:00:00Z',experience:'편의점 경력 우대',tasks:['상품 진열','계산 및 매장 정리']},
]
