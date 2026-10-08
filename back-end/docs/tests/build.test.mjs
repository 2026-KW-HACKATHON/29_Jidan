import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { buildDocs } from '../build.mjs';

async function fixture(t, source = 'openapi: 3.1.0\ninfo: {title: 테스트, version: 1.0.0}\npaths: {/api/health: {get: {responses: {}}}}\n') {
  const directory = await mkdtemp(join(tmpdir(), 'jidan-static-docs-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const specPath = join(directory, 'openapi.yaml');
  if (source !== null) await writeFile(specPath, source);
  return { directory, specPath, outputDir: join(directory, 'public'), source };
}

test('정적 Swagger: 공개 파일만 빌드·YAML/JSON/커밋/해시 일치', async t => {
  const f = await fixture(t);
  const revision = 'a'.repeat(40);
  const info = await buildDocs({ ...f, revision });
  assert.deepEqual((await readdir(f.outputDir)).sort(), ['build-info.json', 'index.html', 'init.js', 'openapi.json', 'openapi.yaml', 'swagger-ui-bundle.js', 'swagger-ui-standalone-preset.js', 'swagger-ui.css'].sort());
  assert.equal(await readFile(join(f.outputDir, 'openapi.yaml'), 'utf8'), f.source);
  assert.equal(JSON.parse(await readFile(join(f.outputDir, 'openapi.json'), 'utf8')).info.title, '테스트');
  assert.equal(info.operationCount, 1);
  assert.equal(info.revision, revision);
  assert.equal(info.specSha256, createHash('sha256').update(f.source).digest('hex'));
  assert.deepEqual(JSON.parse(await readFile(join(f.outputDir, 'build-info.json'), 'utf8')), info);
  assert.match(await readFile(join(f.outputDir, 'init.js'), 'utf8'), /supportedSubmitMethods:\[\]/);
  const html = await readFile(join(f.outputDir, 'index.html'), 'utf8');
  for (const [, link] of html.matchAll(/(?:src|href)="\.\/([^"]+)"/g)) assert.ok((await readFile(join(f.outputDir, link))).length);
});
test('정적 Swagger: 기존 파일이 있는 출력에는 덮어쓰기하지 않음', async t => {
  const f = await fixture(t);
  await buildDocs(f);
  const before = await readFile(join(f.outputDir, 'openapi.json'), 'utf8');
  await assert.rejects(buildDocs(f), /비어 있어야/);
  assert.equal(await readFile(join(f.outputDir, 'openapi.json'), 'utf8'), before);
});
test('정적 Swagger: 명세 누락·문법 오류·필수 필드 오류는 공개 디렉터리 생성 전 거절', async t => {
  for (const source of [null, 'openapi: [', '[]', 'openapi: 3.1.0\ninfo: {}\npaths: []']) {
    const f = await fixture(t, source);
    await assert.rejects(buildDocs(f));
    await assert.rejects(readdir(f.outputDir), { code: 'ENOENT' });
  }
});
test('정적 Swagger: 출력 경로·revision 입력 오류와 파일을 출력으로 사용하는 경우 거절', async t => {
  const f = await fixture(t);
  await assert.rejects(buildDocs({ ...f, outputDir: '' }), /출력 디렉터리/);
  await assert.rejects(buildDocs({ ...f, revision: '../production' }), /revision/);
  await assert.rejects(buildDocs({ ...f, outputDir: f.specPath }), { code: 'ENOTDIR' });
});
