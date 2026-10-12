#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
revision=${1:?revision required}
frontend=${2:?frontend digest required}
backend=${3:?backend digest required}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$frontend" =~ ^ghcr\.io/2026-kw-hackathon/29_jidan-demo-frontend@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ "$backend" =~ ^ghcr\.io/2026-kw-hackathon/29_jidan-demo-backend@sha256:[a-f0-9]{64}$ ]] || exit 2
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=${JIDAN_DEMO_ROOT:-/home/ubuntu/apps/jidan/demo}
mkdir -p "$root/releases"
root=$(cd "$root" && pwd -P)
exec 9>"$root/deploy.lock"
flock -w 300 9
compose() {
  local directory=$1
  shift
  docker compose --project-name jidan-demo --env-file "$directory/images.env" -f "$directory/compose.yml" "$@"
}
previous=
release=
rollback() {
  local result=0
  if [[ -n "$previous" && -d "$previous" ]]; then
    compose "$previous" up -d --wait --wait-timeout 240 || result=$?
  elif [[ -n "$release" && -d "$release" ]]; then
    compose "$release" down || result=$?
  fi
  if (( result == 0 )); then rm -f "$root/pending"; fi
  return "$result"
}
# Recover a job interrupted after it began changing containers; never overwrite that record.
if [[ -f "$root/pending" ]]; then
  { IFS= read -r previous; IFS= read -r release; } < "$root/pending"
  [[ -z "$previous" || "$previous" == "$root/releases/"* ]] || exit 2
  [[ "$release" == "$root/releases/"* ]] || exit 2
  rollback || exit 1
fi
if [[ -L "$root/current" ]]; then
  previous=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$root/current")
  [[ "$previous" == "$root/releases/"* && -d "$previous" ]] || exit 2
else previous=; fi
containers=()
while IFS= read -r container; do containers+=("$container"); done < <(docker ps -aq)
if (( ${#containers[@]} )); then
  docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{json .HostConfig.PortBindings}}' "${containers[@]}" | python3 "$script_dir/check_ports.py"
fi
test -f "$root/runtime.env" && test -f "$root/mysql.env"
release=$(mktemp -d "$root/releases/$revision.XXXXXX")
cp "$script_dir/compose.yml" "$script_dir/proxy.conf" "$release/"
# The unprivileged Nginx container reads this bind mount as uid 101.
# Only the public proxy configuration is readable; env and release remain private.
chmod 644 "$release/proxy.conf"
cp "$root/runtime.env" "$root/mysql.env" "$release/"
printf 'FRONTEND_IMAGE=%s\nBACKEND_IMAGE=%s\n' "$frontend" "$backend" > "$release/images.env"
compose "$release" config --quiet
compose "$release" pull
printf '%s\n%s\n' "$previous" "$release" > "$root/pending"
failed() {
  trap - ERR INT TERM
  echo 'Demo deploy failed; restoring previous Compose images' >&2
  rollback || echo 'Demo rollback failed; pending record retained' >&2
  exit 1
}
trap failed ERR INT TERM
compose "$release" up -d --wait --wait-timeout 240 mysql
# Server only runs a published image: migration/seed are commands, never source builds.
compose "$release" run --rm --no-deps owner sh -ec 'python -m alembic upgrade head && python -m app.demo_seed_public && python -m app.demo_retention'
compose "$release" up -d --wait --wait-timeout 240
for component in frontend worker owner; do
  container=$(compose "$release" ps -q "$component")
  actual=$(docker inspect --format '{{.Config.Image}}' "$container")
  expected=$backend
  if [[ "$component" == frontend ]]; then expected=$frontend; fi
  [[ "$actual" == "$expected" ]] || failed
done
curl -fsS --max-time 20 http://127.0.0.1:3044/healthz >/dev/null
for domain in jidan-ilban-demo.leehyowon14.dev jidan-jumju-demo.leehyowon14.dev; do
  curl -fsS --max-time 20 "https://$domain/api/health" >/dev/null
  role=OWNER
  if [[ "$domain" == jidan-ilban-demo.leehyowon14.dev ]]; then role=WORKER; fi
  curl -fsS --max-time 20 -X POST -H "Origin: https://$domain" -H 'Content-Type: application/json' \
    -d '{}' -o "$release/probe.json" "https://$domain/api/demo/session"
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["role"] == sys.argv[2]' "$release/probe.json" "$role"
  rm -f "$release/probe.json"
done
python3 - "$root" "$release" <<'PY'
import os,sys
from pathlib import Path
root,release=Path(sys.argv[1]),Path(sys.argv[2])
temporary=root/'current.next'
temporary.unlink(missing_ok=True)
temporary.symlink_to(release)
os.replace(temporary,root/'current')
PY
rm -f "$root/pending"
trap - ERR INT TERM
echo "Demo deployed: $revision"
