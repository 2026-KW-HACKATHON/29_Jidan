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
# How long the next deployment waits for a recorded migration that is still running.
migration_wait=${JIDAN_MIGRATION_WAIT_SECONDS:-300}
if [[ ! "$migration_wait" =~ ^[1-9][0-9]{0,3}$ ]]; then
  echo 'Invalid JIDAN_MIGRATION_WAIT_SECONDS' >&2
  exit 2
fi
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
# Docker keeps a one-off container running independently of a killed Compose client.
# Keep the journal until the daemon confirms the recorded container is gone. A container that is
# still migrating is never killed: MySQL DDL is not transactional, so a forced stop could leave a
# half-applied revision for the next upgrade. $1 is how long to wait for it (0: do not wait).
recover_migration() {
  [[ -f "$root/migration.pending" ]] || return 0
  local wait_limit=${1:-0} name containers attempt state code
  name=$(cat "$root/migration.pending")
  if [[ ! "$name" =~ ^${project}-migrate-[a-zA-Z0-9]{8}$ ]]; then
    echo 'Invalid migration recovery record; deployment stopped.' >&2
    return 1
  fi
  # A killed client may leave an in-flight create request. Absence alone cannot
  # prove completion: allow delayed creation to appear, then fail closed if unknown.
  for attempt in {1..10}; do
    containers=$(docker container ls -a --filter "name=^/${name}$" --format '{{.Names}}') || return 1
    if [[ -n "$containers" ]]; then
      [[ "$containers" == "$name" ]] || return 1
      break
    fi
    if [[ "$attempt" == 10 ]]; then
      echo 'Migration creation/completion is uncertain; recovery record retained for operator verification.' >&2
      return 1
    fi
    sleep 1
  done
  state=$(docker container inspect --format '{{.State.Running}} {{.State.ExitCode}}' "$name") || return 1
  code=${state#* }
  if [[ "${state%% *}" == true ]]; then
    if [[ "$wait_limit" == 0 ]]; then
      echo 'Migration container is still running and was left alone; the next deployment waits for it.' >&2
      return 1
    fi
    echo "Waiting up to ${wait_limit}s for the recorded migration container to finish." >&2
    if ! code=$(timeout "$wait_limit" docker wait "$name"); then
      echo 'Recorded migration did not finish in time; record retained. Check it with `docker ps` and retry later.' >&2
      return 1
    fi
  fi
  # A created-but-never-started container reports 0. Any other code is a failed migration.
  if [[ "$code" != 0 ]]; then
    echo 'Recorded migration failed; record retained. Check `alembic current` and the schema, then remove migration.pending.' >&2
    return 1
  fi
  # Only a container that is no longer running (finished, or created and never started) is removed.
  containers=$(docker container ls -a --filter "name=^/${name}$" --format '{{.Names}}') || return 1
  if [[ -n "$containers" ]]; then
    docker rm "$name" >/dev/null || return 1
    containers=$(docker container ls -a --filter "name=^/${name}$" --format '{{.Names}}') || return 1
    [[ -z "$containers" ]] || return 1
  fi
  rm -f "$root/migration.pending" "$root/migration.next"
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
  # An interrupted `compose run` client can leave its one-off container migrating.
  if ! recover_migration; then
    echo 'MIGRATION RECOVERY FAILED: record retained; retry deployment to recover.' >&2
    # Keep an interrupt's 130/143: leaving a running migration alone is the expected outcome.
    [[ "$status" != 0 ]] || status=1
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
recover_migration "$migration_wait"
recover_pending
release=$(mktemp -d "$root/releases/release.XXXXXXXX")
cp "deploy/$component/compose.yml" "$release/compose.yml"
printf 'IMAGE_REF=%s\nAPP_PORT=%s\n' "$image" "$port" > "$release/.env"
if [[ "$component" == backend ]]; then
  install -m 600 "$root/runtime.env" "$release/runtime.env"
  # One fixed volume per environment keeps uploaded media across releases and rollbacks.
  printf 'MEDIA_VOLUME=jidan-%s-media\n' "$environment" >> "$release/.env"
  run python3 "$script_dir/check_runtime_env.py" "$environment" "$release/runtime.env"
fi
compose "$release" config --quiet
compose "$release" pull
# Only dev backend migrates automatically, before any container changes. Production stays manual.
if [[ "$environment/$component" == dev/backend ]]; then
  # The journal, not a shared name, prevents overlap: a run left behind by a SIGKILLed runner
  # keeps migrating, so the next run waits for exactly the recorded container first.
  migration_container="$project-migrate-${release##*.}"
  printf '%s\n' "$migration_container" > "$root/migration.next"
  mv -Tf "$root/migration.next" "$root/migration.pending"
  if ! compose "$release" run --rm --no-deps -T --name "$migration_container" backend python -m alembic upgrade head; then
    echo 'DB migration failed; the running release was left untouched and the new release was not deployed.' >&2
    echo 'MySQL DDL is not transactional: check `alembic current` and the schema before retrying.' >&2
    exit 1
  fi
  # Success from the attached --rm client confirms this create/run completed.
  # A crash before this removal deliberately leaves an ambiguous recovery record.
  rm -f "$root/migration.pending" "$root/migration.next"
fi
ln -s "$release" "$root/pending"
compose "$release" up -d --wait --wait-timeout 180
container=$(compose "$release" ps -q "$component")
expected=$(docker image inspect "$image" --format '{{.Id}}')
actual=$(docker inspect "$container" --format '{{.Image}}')
[[ -n "$container" && "$expected" == "$actual" ]]
if [[ "$component" == backend ]]; then
  # The app user must be able to write the mounted media volume; prints nothing from the container.
  run docker exec "$container" python -c \
    'import os, tempfile; tempfile.TemporaryFile(dir=os.environ["MEDIA_ROOT"]).close()'
  if [[ "$environment" == dev ]]; then
    origin=https://dev-jidan.leehyowon14.dev
  else
    origin=https://jidan.leehyowon14.dev
  fi
  # Validate effective settings without displaying any container environment values.
  run docker exec "$container" python -c \
    'import os, sys
from app.admin_password_config import parse_password_hash
try:
    parse_password_hash(os.getenv("ADMIN_PASSWORD_HASH", ""))
except ValueError:
    sys.exit("Invalid backend administrator password configuration")
sys.exit(os.getenv("ALLOWED_ORIGINS") != sys.argv[1])' "$origin"
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
