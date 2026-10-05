import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { parse } from 'yaml';
import { docsHtml, docsInit } from './ui.mjs';

const require = createRequire(import.meta.url);
const swaggerRoot = dirname(require.resolve('swagger-ui-dist/package.json'));
const defaultSpec = fileURLToPath(new URL('../openapi.yaml', import.meta.url));
const methods = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace']);

export async function buildDocs({ outputDir, specPath = defaultSpec, revision = 'uncommitted' }) {
  if (!outputDir) throw new Error('출력 디렉터리가 필요합니다.');
  if (!/^(?:[a-f0-9]{40}|uncommitted)$/.test(revision)) throw new Error('잘못된 문서 revision입니다.');
  const target = resolve(outputDir);
  try {
    if ((await readdir(target)).length) throw new Error('출력 디렉터리는 비어 있어야 합니다.');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const source = await readFile(specPath, 'utf8');
  const spec = parse(source);
  if (!spec || spec.openapi !== '3.1.0' || !spec.info?.title || !spec.info?.version ||
      !spec.paths || typeof spec.paths !== 'object' || Array.isArray(spec.paths)) {
    throw new Error('유효한 OpenAPI 3.1 명세가 필요합니다.');
  }
  const assets = ['swagger-ui.css', 'swagger-ui-bundle.js', 'swagger-ui-standalone-preset.js'];
  const files = Object.fromEntries(await Promise.all(assets.map(async name => [name, await readFile(resolve(swaggerRoot, name))])));
  const buildInfo = {
    revision,
    version: spec.info.version,
    operationCount: Object.values(spec.paths).reduce((sum, value) => sum + Object.keys(value).filter(x => methods.has(x)).length, 0),
    specSha256: createHash('sha256').update(source).digest('hex'),
    builtAt: new Date().toISOString(),
  };
  Object.assign(files, {
    'index.html': docsHtml,
    'init.js': docsInit,
    'openapi.yaml': source,
    'openapi.json': JSON.stringify(spec),
    'build-info.json': JSON.stringify(buildInfo),
  });
  await mkdir(target, { recursive: true });
  for (const [name, contents] of Object.entries(files)) await writeFile(resolve(target, name), contents, { flag: 'wx' });
  return buildInfo;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3) throw new Error('사용법: npm run build -- <빈 출력 디렉터리>');
  const info = await buildDocs({ outputDir: process.argv[2], revision: process.env.DOCS_REVISION ?? 'uncommitted' });
  console.log(JSON.stringify(info));
}
