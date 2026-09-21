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
previous=$(readlink -f "$root/current" 2>/dev/null || true)
release=$(mktemp -d "$root/releases/release.XXXXXXXX")
cp "deploy/$component/compose.yml" "$release/compose.yml"
printf 'IMAGE_REF=%s\nAPP_PORT=%s\n' "$image" "$port" > "$release/.env"
if [[ "$component" == backend ]]; then
  install -m 600 "$root/runtime.env" "$release/runtime.env"
fi
compose() {
  local directory=$1
  shift
  docker compose -p "$project" --env-file "$directory/.env" -f "$directory/compose.yml" "$@"
}
# A pull failure leaves the current deployment untouched.
compose "$release" config --quiet
compose "$release" pull
rollback() {
  local status=$?
  trap - ERR
  echo 'Deployment failed; restoring previous component release.' >&2
  if [[ -n "$previous" && -f "$previous/compose.yml" ]]; then
    compose "$previous" up -d --wait --wait-timeout 180 || echo 'ROLLBACK FAILED: manual intervention required' >&2
  else
    compose "$release" down || true
  fi
  exit "$status"
}
trap rollback ERR
compose "$release" up -d --wait --wait-timeout 180
container=$(compose "$release" ps -q "$component")
expected=$(docker image inspect "$image" --format '{{.Id}}')
actual=$(docker inspect "$container" --format '{{.Image}}')
[[ -n "$container" && "$expected" == "$actual" ]]
if [[ "$component" == backend ]]; then health=/api/health; else health=/healthz; fi
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:$port$health" >/dev/null
curl --fail --silent --show-error --max-time 20 --retry 3 "$url" >/dev/null
ln -s "$release" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
trap - ERR
echo "Deployed $environment/$component: $image"
