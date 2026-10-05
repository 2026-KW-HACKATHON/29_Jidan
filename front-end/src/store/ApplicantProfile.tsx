import { AppBar } from '../ui/AppBar'
import { MobileLayout } from '../ui/MobileLayout'
import { ResponsiveSentences } from '../ui/ResponsiveSentences'
import { applicantAge } from './applicantAge'
import chick from '../brand/assets/chick-sitting.png'
import './ApplicantProfile.css'

export type ApplicantExperience = { id: string; title: string; period: string; duties: string }
export type ApplicantProfileData = {
  name: string
  birth: string
  experienceSummary: string
  application: { title: string; schedule: string }
  introduction: string
  experiences: ApplicantExperience[]
}

export function ApplicantIdentity({ data, currentDate }: { data: Pick<ApplicantProfileData, 'name' | 'birth' | 'experienceSummary'>; currentDate?: string }) {
  const age = applicantAge(data.birth, currentDate)
  return <section className="applicant-card applicant-identity" aria-label="근무자 기본 정보">
    <img src={chick} width="60" height="58" alt="" />
    <div><div className="applicant-name"><h2>{data.name}</h2>{age !== undefined && <span>(만 {age}세)</span>}</div><p>{data.experienceSummary}</p></div>
  </section>
}

export function ApplicantApplication({ application }: { application: ApplicantProfileData['application'] }) {
  return <section className="applicant-application" aria-label="지원한 공고"><p>지원한 공고</p><h3>{application.title}</h3><p>{application.schedule}</p></section>
}

export function ApplicantIntroduction({ introduction }: { introduction: string }) {
  return <section className="applicant-card" aria-label="지원자 소개서"><h3>지원자 소개서</h3><ResponsiveSentences lines={introduction.split('\n')} /></section>
}

export function ApplicantExperienceCard({ experience }: { experience: ApplicantExperience }) {
  return <section className="applicant-card applicant-experience" aria-label={experience.title}><h3>{experience.title}</h3><p>{experience.period}</p><p>{experience.duties}</p></section>
}

/** Display-only: authentication and fetching belong to the future API integration. */
export function ApplicantProfile({ data, onBack, currentDate }: { data: ApplicantProfileData; onBack: () => void; currentDate?: string }) {
  return <MobileLayout className="applicant-profile" header={<AppBar compact title="근무자 프로필" onBack={onBack} />}>
    <div className="applicant-content">
      <ApplicantIdentity data={data} currentDate={currentDate} />
      <ApplicantApplication application={data.application} />
      <ApplicantIntroduction introduction={data.introduction} />
      {data.experiences.map(experience => <ApplicantExperienceCard key={experience.id} experience={experience} />)}
      <p className="applicant-notice">경력은 근무자가 직접 등록한 정보예요. 지원한 날짜의 근무 가능 여부를 확인해 주세요.</p>
    </div>
  </MobileLayout>
}
