FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

FROM dependencies AS test
ENV CI=true
RUN npm run lint && npm run test:ci

FROM dependencies AS build
# Preview is opt-in for dev only. Never embed secrets in VITE_* variables.
ARG VITE_ENABLE_PREVIEW=false
ENV VITE_ENABLE_PREVIEW=$VITE_ENABLE_PREVIEW
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
RUN printf '%s\n' \
  'server {' \
  '  listen 8080;' \
  '  server_name _;' \
  '  root /usr/share/nginx/html;' \
  '  location = /healthz { access_log off; return 200 "ok"; }' \
  '  location /assets/ { try_files $uri =404; }' \
  '  location / { try_files $uri $uri/ /index.html; }' \
  '}' > /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
