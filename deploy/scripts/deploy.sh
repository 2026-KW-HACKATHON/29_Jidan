#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

environment=${1:?environment required}
component=${2:?component required}
image=${3:?image digest required}
case "$environment" in dev|production) ;; *) exit 2 ;; esac
case "$component" in frontend|backend) ;; *) exit 2 ;; esac
if [[ ! "$image" =~ ^ghcr\.io/2026-kw-hackathon/29_jidan-${component}@sha256:[a-f0-9]{64}$ ]]; then
  echo 'Invalid image reference' >&2
  exit 2
fi
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root="${JIDAN_APP_ROOT:-/home/ubuntu/apps/jidan}/$environment/$component"
project="jidan-$environment-$component"
case "$environment/$component" in
  dev/frontend) port=3020; url=https://dev-jidan.leehyowon14.dev/ ;;
  dev/backend) port=3021; url=https://dev-jidan.leehyowon14.dev/api/health ;;
  production/frontend) port=3022; url=https://jidan.leehyowon14.dev/ ;;
  production/backend) port=3023; url=https://jidan.leehyowon14.dev/api/health ;;
esac
mkdir -p "$root/releases"
exec 9>"$root/deploy.lock"
flock -w 300 9
child=
run() {
  "$@" &
  child=$!
  local status=0
  wait "$child" || status=$?
  child=
  return "$status"
}
compose() {
  local directory=$1
  shift
  run docker compose -p "$project" --env-file "$directory/.env" -f "$directory/compose.yml" "$@"
}
# Persist intent before touching containers. SIGKILL/power loss is recovered on the next run.
recover_pending() {
  [[ -L "$root/pending" ]] || return 0
  local candidate current
  candidate=$(readlink -f "$root/pending")
  current=$(readlink -f "$root/current" 2>/dev/null || true)
  if [[ "$candidate" != "$current" ]]; then
    echo 'Recovering interrupted deployment.' >&2
    if [[ -n "$current" && -f "$current/compose.yml" ]]; then
      compose "$current" up -d --wait --wait-timeout 90 || return 1
    else
      compose "$candidate" down || return 1
    fi
  fi
  rm -f "$root/pending" "$root/current.next"
}
cleanup() {
  local status=$?
  trap - EXIT
  trap '' INT TERM
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    child=
  fi
  if ! recover_pending; then
    echo 'ROLLBACK FAILED: pending marker retained; retry deployment to recover.' >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Never overwrite evidence of an interrupted deployment before recovery succeeds.
recover_pending
release=$(mktemp -d "$root/releases/release.XXXXXXXX")
cp "deploy/$component/compose.yml" "$release/compose.yml"
printf 'IMAGE_REF=%s\nAPP_PORT=%s\n' "$image" "$port" > "$release/.env"
if [[ "$component" == backend ]]; then
  install -m 600 "$root/runtime.env" "$release/runtime.env"
  run python3 "$script_dir/check_runtime_env.py" "$environment" "$release/runtime.env"
fi
compose "$release" config --quiet
compose "$release" pull
ln -s "$release" "$root/pending"
compose "$release" up -d --wait --wait-timeout 180
container=$(compose "$release" ps -q "$component")
expected=$(docker image inspect "$image" --format '{{.Id}}')
actual=$(docker inspect "$container" --format '{{.Image}}')
[[ -n "$container" && "$expected" == "$actual" ]]
if [[ "$component" == backend ]]; then
  if [[ "$environment" == dev ]]; then
    origin=https://dev-jidan.leehyowon14.dev
  else
    origin=https://jidan.leehyowon14.dev
  fi
  # Inspect only this setting and report no container environment values.
  run docker exec "$container" python -c \
    'import os, sys; sys.exit(os.getenv("ALLOWED_ORIGINS") != sys.argv[1])' "$origin"
fi
if [[ "$component" == backend ]]; then health=/api/health; else health=/healthz; fi
run curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:$port$health" >/dev/null
run curl --fail --silent --show-error --max-time 20 --retry 3 "$url" >/dev/null
# Development Swagger is part of the exact backend image, not a separate manual deployment.
if [[ "$environment/$component" == dev/backend ]]; then
  for doc_path in /api/swagger/ /api/swagger/openapi.json; do
    run curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:$port$doc_path" >/dev/null
    run curl --fail --silent --show-error --max-time 20 --retry 3 "https://dev-jidan.leehyowon14.dev$doc_path" >/dev/null
  done
fi
touch "$release/verified"
ln -s "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
# Once current changes, recovery recognizes this release as committed.
rm -f "$root/pending"
echo "Deployed $environment/$component: $image"
if ! run python3 "$script_dir/retention.py" deploy "$environment" "$component"; then
  echo 'Retention cleanup failed; deployment remains committed.' >&2
fi
