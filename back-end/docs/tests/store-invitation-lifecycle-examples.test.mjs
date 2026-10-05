import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec } from './helpers/owner-contract.mjs';
const op = spec.paths['/api/stores/{storeId}/invitations'].get;
const pages = Object.values(op.responses['200'].content['application/json'].examples).map(e => e.value);
function* records(value) {
  if (!value || typeof value !== 'object') return;
  if ('email' in value && 'createdAt' in value && 'expiresAt' in value && 'status' in value) { yield value; return; }
  for (const child of Object.values(value)) yield* records(child);
}
test('초대 전체 응답 예시: 7일 링크 기한과 생성 이후 전이 시각 일치', () => {
  for (const path of Object.values(spec.paths)) for (const operation of Object.values(path)) {
    if (!operation.tags.includes('근무자 초대')) continue;
    for (const response of Object.values(operation.responses)) for (const media of Object.values(response.content ?? {})) {
      const values = 'example' in media ? [media.example] : Object.values(media.examples ?? {}).map(e => e.value);
      for (const value of values) for (const record of records(value)) {
        const start = Date.parse(record.createdAt); const expiry = Date.parse(record.expiresAt);
        assert.equal(expiry - start, 7 * 24 * 60 * 60 * 1000);
        assert.ok(Date.parse(record.lastSentAt) >= start && Date.parse(record.lastSentAt) < expiry);
        for (const field of ['acceptedAt', 'declinedAt', 'canceledAt']) {
          if (record[field] !== null) assert.ok(Date.parse(record[field]) >= start && Date.parse(record[field]) < expiry, field);
        }
      }
    }
  }
});
test('초대 목록 예시: UUID 중복 없음·스냅샷과 상태 시간 일치·건수 일치', () => {
  for (const page of pages) {
    assert.equal(new Set(page.items.map(v => v.id)).size, page.items.length);
    assert.ok(page.totalItems >= page.items.length);
    const now = Date.parse(page.asOf);
    for (const invitation of page.items) {
      const deadline = Math.min(Date.parse(invitation.expiresAt), invitation.accessExpiresAt ? Date.parse(invitation.accessExpiresAt) : Infinity);
      assert.ok(Date.parse(invitation.createdAt) <= now);
      if (invitation.status === 'PENDING') assert.ok(now < deadline);
      if (invitation.status === 'EXPIRED') assert.ok(now >= deadline);
      for (const field of ['acceptedAt', 'declinedAt', 'canceledAt']) if (invitation[field]) assert.ok(Date.parse(invitation[field]) <= now);
    }
  }
});
