import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spec } from './helpers/owner-contract.mjs';

test('API 서버: 개발 도메인 우선·프로덕션 서버 선택지 제외', () => {
  assert.equal(spec.servers[0].url, 'https://dev-jidan.leehyowon14.dev');
  assert.equal(spec.servers[1].url, 'http://127.0.0.1:8000');
  assert.equal(spec.servers.length, 2);
});
test('API endpoint: 서버 URL과 경로를 합쳐도 /api가 중복되지 않음', () => {
  for (const server of spec.servers) {
    assert.equal(new URL(server.url).pathname, '/');
    for (const path of Object.keys(spec.paths)) {
      assert.ok(path.startsWith('/api/'), path);
      const resolvedPath = path.replace(/\{[^}]+\}/g, "00000000-0000-4000-8000-000000000000");
      const endpoint = new URL(server.url + resolvedPath);
      assert.equal(endpoint.pathname, resolvedPath);
      assert.ok(!endpoint.pathname.startsWith('/api/api/'), path);
    }
  }
});
