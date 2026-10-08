export const docsHtml = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>지단 API 명세</title><link rel="stylesheet" href="./swagger-ui.css">
<style>body{margin:0}.notice{padding:14px 24px;background:#edf2ff;font:16px sans-serif;color:#183b93}</style>
</head><body><div class="notice">지단 API 설계 문서 · 실제 인증 API는 아직 구현되지 않았습니다.</div>
<div id="swagger-ui"></div><script src="./swagger-ui-bundle.js"></script>
<script src="./swagger-ui-standalone-preset.js"></script><script src="./init.js"></script></body></html>`;
export const docsInit = `window.ui = SwaggerUIBundle({url:'./openapi.json',dom_id:'#swagger-ui',
  presets:[SwaggerUIBundle.presets.apis,SwaggerUIStandalonePreset],layout:'StandaloneLayout',
  deepLinking:true,displayRequestDuration:true,validatorUrl:null,supportedSubmitMethods:[],
  persistAuthorization:false});`;
