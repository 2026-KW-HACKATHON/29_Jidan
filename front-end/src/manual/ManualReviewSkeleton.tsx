/** Placeholder only: never invent titles or content while the server prepares a review. */
export function ManualReviewSkeleton() {
 return <div className="manual-stack" aria-busy="true" aria-label="요약 카드 불러오는 중">
  <div className="manual-card manual-review-skeleton" aria-hidden="true">
   <span className="manual-skeleton-line manual-skeleton-title"/>
   <span className="manual-skeleton-line"/>
   <span className="manual-skeleton-line"/>
   <span className="manual-skeleton-line manual-skeleton-short"/>
  </div>
 </div>
}
