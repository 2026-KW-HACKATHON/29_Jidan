import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDocsServer } from '../server.mjs';

async function fixture(t, source = 'openapi: 3.1.0\ninfo:\n  title: 테스트\n  version: 1.0.0\npaths: {}\n') {
  const dir = await mkdtemp(join(tmpdir(), 'jidan-docs-'));
  const specPath = join(dir, 'openapi.yaml');
  if (source !== null) await writeFile(specPath, source);
  const server = createDocsServer({ specPath });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    await new Promise(resolve => server.close(resolve));
    await rm(dir, { recursive: true, force: true });
  });
  return `http://127.0.0.1:${server.address().port}`;
}

test('문서와 로컬 Swagger 자산을 제공하며 실행 기능을 비활성화', async t => {
  const url = await fixture(t);
  for (const path of ['/', '/docs', '/init.js', '/swagger-ui.css', '/swagger-ui-bundle.js', '/swagger-ui-standalone-preset.js']) {
    const response = await fetch(url + path);
    assert.equal(response.status, 200, path);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  }
  assert.match(await (await fetch(url + '/init.js')).text(), /supportedSubmitMethods:\[\]/);
});
test('UTF-8 YAML과 파싱된 JSON 명세 제공 및 HEAD 본문 생략', async t => {
  const url = await fixture(t);
  assert.match(await (await fetch(url + '/openapi.yaml')).text(), /테스트/);
  const response = await fetch(url + '/openapi.json');
  assert.equal(response.headers.get('content-type'), 'application/json');
  assert.equal((await response.json()).info.title, '테스트');
  const head = await fetch(url + '/openapi.yaml', { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
});
test('API 실행, 없는 경로, 파일 경로 접근 거절', async t => {
  const url = await fixture(t);
  for (const path of ['/api/auth/logout', '/package.json', '/server.mjs', '/%2e%2e%2fserver.mjs']) {
    assert.equal((await fetch(url + path)).status, 404, path);
  }
  const response = await fetch(url + '/openapi.yaml', { method: 'POST' });
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('allow'), 'GET, HEAD');
});
test('명세 누락/잘못된 YAML은 경로와 오류 상세를 노출하지 않는 500 응답', async t => {
  for (const source of [null, 'openapi: [']) {
    const url = await fixture(t, source);
    const response = await fetch(url + '/openapi.json');
    assert.equal(response.status, 500);
    assert.doesNotMatch(await response.text(), /jidan-docs-|ENOENT|YAMLParseError/);
  }
});
