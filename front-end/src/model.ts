export type Role = 'owner' | 'member' | null
export type Career = { id: string; industry: string; task: string; start: string; end: string; current: boolean; storeName?: string }
export type Availability = { id: string; day: number; start: string; end: string }
export type Profile = { name: string; email: string; phone: string; birthDate: string; gender: string; experience: 'new' | 'experienced'; careers: Career[]; availability: Availability[] }
export type Worker = { id: string; name: string; email: string; task: string; type: 'regular' | 'temporary'; status: 'active' | 'ended'; startDate: string; endDate: string }
export type Invite = { id: string; name: string; email: string; task: string; status: 'pending' | 'accepted' | 'cancelled'; createdAt: string }
export type ManualAttachment = { step: number; name: string; dataUrl: string }
export type Manual = { id: string; title: string; category: string; description: string; steps: string[]; status: 'draft' | 'published'; updatedAt: string; publishedSteps?: string[]; publishedTitle?: string; sourceText?: string; attachments?: ManualAttachment[]; publishedAttachments?: ManualAttachment[] }
export type Applicant = { id: string; name: string; email: string; experience: string; status: 'pending' | 'confirmed' | 'withdrawn' }
export type Job = { id: string; storeId: string; storeName: string; title: string; category: string; date: string; start: string; end: string; wage: number; task: string; experience: string; description: string; capacity: number; status: 'open' | 'closed'; applicants: Applicant[] }
export type AppData = { role: Role; store: { id: string; name: string; ownerName: string; category: string; address: string; phone: string; businessNumber: string; status: 'active' | 'pending' }; profile: Profile; workers: Worker[]; invites: Invite[]; manuals: Manual[]; jobs: Job[]; readNotifications: boolean; savedJobs: string[] }
export const STORAGE_KEY = 'jidan-prototype-v1'
export function futureDate(days: number) { const date = new Date(); date.setDate(date.getDate() + days); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
export function createDemoData(): AppData {
  const storeName = '명랑핫도그 광운대점'
  return {
    role: null,
    store: { id: 'store-1', name: storeName, ownerName: '김민지', category: '음식점', address: '서울 노원구 광운로 20 (월계1동)', phone: '02-1234-5678', businessNumber: '123-45-67890', status: 'active' },
    profile: { name: '김지수', email: 'jisu@example.com', phone: '010-1234-5678', birthDate: '2001-05-15', gender: '여성', experience: 'experienced', careers: [{ id: 'career-1', industry: '음식점', task: '홀 서빙', start: '2025-03', end: '', current: true, storeName }], availability: [{ id: 'time-1', day: 1, start: '09:00', end: '14:00' }, { id: 'time-2', day: 3, start: '09:00', end: '14:00' }, { id: 'time-3', day: 5, start: '09:00', end: '14:00' }] },
    workers: [
      { id: 'worker-1', name: '김지수', email: 'jisu@example.com', task: '홀 서빙', type: 'regular', status: 'active', startDate: '2026-07-01', endDate: '' },
      { id: 'worker-2', name: '이민준', email: 'minjun@example.com', task: '매장 마감', type: 'regular', status: 'active', startDate: '2026-08-01', endDate: '' },
      { id: 'worker-3', name: '박서연', email: 'seoyeon@example.com', task: '상품 입고·진열', type: 'regular', status: 'active', startDate: '2026-08-15', endDate: '' },
      { id: 'worker-4', name: '최유진', email: 'yujin@example.com', task: '홀 서빙', type: 'temporary', status: 'active', startDate: futureDate(-1), endDate: `${futureDate(2)}T14:00` },
    ],
    invites: [{ id: 'invite-demo', name: '김지수', email: 'jisu@example.com', task: '매장 마감', status: 'pending', createdAt: new Date().toISOString() }],
    manuals: [
      { id: 'manual-1', title: '처음 시작하는 홀 서빙', category: '홀 서빙', description: '첫 출근도 차근차근. 고객 응대부터 테이블 정리까지 확인해요.', steps: ['출근 후 손을 씻고 앞치마와 이름표를 착용해 주세요.', '고객이 들어오면 밝게 인사하고 주문을 안내해 주세요.', '주문 내용을 다시 확인하고 준비된 메뉴를 전달해 주세요.', '고객이 떠난 테이블은 정리하고 깨끗한 행주로 닦아 주세요.'], status: 'published', updatedAt: futureDate(-1) },
      { id: 'manual-2', title: '매장 마감 체크리스트', category: '매장 마감', description: '하루의 마지막, 빠뜨리지 않고 마무리해요.', steps: ['재고 수량과 유통기한을 확인하고 점주에게 전달해 주세요.', '매장 설비는 정해진 종료 절차에 따라 정리해 주세요.', '쓰레기를 분리 배출하고 바닥을 청소해 주세요.', '점주와 함께 마감 내역을 확인한 뒤 출입문을 잠가 주세요.'], status: 'published', updatedAt: futureDate(-2) },
      { id: 'manual-3', title: '상품 입고·진열 방법', category: '상품 입고·진열', description: '입고 수량과 유통기한을 함께 확인해요.', steps: ['입고 상품을 납품서의 수량과 대조해 주세요.', '수량이 다르면 사진을 남기고 점주에게 확인해 주세요.', '유통기한이 빠른 상품을 앞쪽에 진열해 주세요.'], status: 'draft', updatedAt: futureDate(0) },
    ],
    jobs: [
      { id: 'job-1', storeId: 'store-1', storeName, title: '주말 오픈 대타', category: '음식점', date: futureDate(2), start: '09:00', end: '14:00', wage: 12000, task: '홀 서빙', experience: '경력 무관', description: '밝은 인사로 함께 하루를 시작해요. 주문 안내와 홀 정리를 담당해요. 첫 근무 전 매뉴얼을 제공해 드려요.', capacity: 1, status: 'open', applicants: [{ id: 'app-1', name: '이하은', email: 'haeun@example.com', experience: '홀 서빙 · 6개월', status: 'pending' }, { id: 'app-2', name: '정우진', email: 'woojin@example.com', experience: '음식점 · 1년', status: 'pending' }, { id: 'app-3', name: '오수빈', email: 'subin@example.com', experience: '신입', status: 'pending' }] },
      { id: 'job-2', storeId: 'store-1', storeName, title: '평일 마감 대타', category: '음식점', date: futureDate(5), start: '18:00', end: '22:00', wage: 13000, task: '매장 마감', experience: '경력 무관', description: '재고 정리와 홀 청소를 도와주실 분을 찾아요. 점주와 함께 마감해요.', capacity: 1, status: 'open', applicants: [{ id: 'app-4', name: '이지훈', email: 'jihun@example.com', experience: '마감 업무 · 4개월', status: 'pending' }] },
      { id: 'job-3', storeId: 'store-1', storeName, title: '토요일 풀타임', category: '음식점', date: futureDate(9), start: '10:00', end: '18:00', wage: 12000, task: '홀 서빙', experience: '경력 무관', description: '주말 매장 운영을 함께해요. 상세 업무는 온보딩 매뉴얼에서 확인할 수 있어요.', capacity: 1, status: 'open', applicants: [] },
    ],
    readNotifications: false, savedJobs: [],
  }
}
export function hasMemberAccess(data: AppData, task?: string) {
  return data.workers.some(worker => worker.email.toLowerCase() === data.profile.email.toLowerCase() && worker.status === 'active' && (!task || worker.task.split(',').map(value => value.trim()).includes(task)) && (!worker.startDate || new Date(worker.startDate).getTime() <= Date.now()) && (worker.type === 'regular' || (!!worker.endDate && new Date(worker.endDate).getTime() > Date.now())))
}
