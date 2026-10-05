import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec } from './helpers/owner-contract.mjs';
const pages = Object.values(spec.paths['/api/stores/{storeId}/workers'].get.responses['200'].content['application/json'].examples).map(e => e.value);
test('근무자 예시: 페이지 내 ID 중복 없음·활성/임박 건수 관계와 시간 일치', () => {
  for (const page of pages) {
    assert.equal(new Set(page.items.map(v => v.workerId)).size, page.items.length);
    assert.ok(page.expiringCount <= page.activeCount);
    assert.ok(page.items.length <= page.size && page.items.length <= page.totalItems);
    const now = Date.parse(page.asOf);
    for (const member of page.items) for (const grant of member.accessGrants) {
      assert.ok(Date.parse(grant.startedAt) <= now);
      if (grant.status === 'ACTIVE' || grant.status === 'EXPIRING') {
        assert.equal(grant.revokedAt, null);
        if (grant.validUntil !== null) assert.ok(Date.parse(grant.validUntil) > now);
      }
      if (grant.status === 'EXPIRING') assert.ok(Date.parse(grant.validUntil) - now <= 24 * 60 * 60 * 1000);
      if (grant.status === 'REVOKED') assert.ok(Date.parse(grant.revokedAt) <= now);
    }
  }
});
test('관리 요약과 완전한 활성 목록 예시: 동일 시각의 서로 다른 근무자 집계 일치', () => {
  const summary = spec.paths['/api/stores/{storeId}/management-summary'].get.responses['200'].content['application/json'].examples.summary.value;
  const active = spec.paths['/api/stores/{storeId}/workers'].get.responses['200'].content['application/json'].examples.active.value;
  assert.equal(summary.asOf, active.asOf);
  assert.equal(summary.activeWorkerCount, active.activeCount);
  assert.equal(summary.expiringWorkerCount, active.expiringCount);
  assert.equal(active.activeCount, active.items.length);
  assert.equal(active.expiringCount, active.items.filter(v => v.accessStatus === 'EXPIRING').length);
});
