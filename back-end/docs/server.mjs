import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { createRequire } from 'node:module';
import { parse } from 'yaml';
import { docsHtml, docsInit } from './ui.mjs';

const require = createRequire(import.meta.url);
const swaggerRoot = dirname(require.resolve('swagger-ui-dist/package.json'));
const defaultSpec = fileURLToPath(new URL('../openapi.yaml', import.meta.url));
const assets = new Map([
  ['/swagger-ui.css', 'text/css; charset=utf-8'],
  ['/swagger-ui-bundle.js', 'text/javascript; charset=utf-8'],
  ['/swagger-ui-standalone-preset.js', 'text/javascript; charset=utf-8'],
]);

export function createDocsServer({ specPath = defaultSpec } = {}) {
  return createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (!['GET', 'HEAD'].includes(req.method)) {
      res.setHeader('Allow', 'GET, HEAD');
      res.writeHead(405).end('문서 서버는 조회만 지원합니다.');
      return;
    }
    const path = new URL(req.url, 'http://127.0.0.1').pathname;
    let body;
    let type;
    try {
      if (path === '/' || path === '/docs') {
        body = docsHtml;
        type = 'text/html; charset=utf-8';
      } else if (path === '/init.js') {
        body = docsInit;
        type = 'text/javascript; charset=utf-8';
      } else if (path === '/openapi.yaml' || path === '/openapi.json') {
        const source = await readFile(specPath, 'utf8');
        body = path.endsWith('.json') ? JSON.stringify(parse(source)) : source;
        type = path.endsWith('.json') ? 'application/json' : 'application/yaml; charset=utf-8';
      } else if (assets.has(path)) {
        body = await readFile(resolve(swaggerRoot, path.slice(1)));
        type = assets.get(path);
      } else {
        res.writeHead(404).end('Not found');
        return;
      }
      res.writeHead(200, { 'Content-Type': type });
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch {
      res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end(req.method === 'HEAD' ? undefined : '문서 파일을 읽을 수 없습니다. 명세와 설치 상태를 확인하세요.');
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT ?? 5500);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT 범위: 1–65535');
  const server = createDocsServer();
  server.on('error', (error) => { console.error(error.message); process.exitCode = 1; });
  server.listen(port, '127.0.0.1', () => console.log(`지단 API 설계 문서: http://127.0.0.1:${port}`));
}
