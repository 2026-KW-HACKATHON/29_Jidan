#!/bin/sh
# Every invocation gets its own project. Cleanup never targets the manual sandbox.
set -eu
cd "$(dirname "$0")"
project="jidan-e2e-$(date +%s)-$$"
repo=$(cd ../.. && pwd)
JIDAN_E2E_REPORT_DIR=${JIDAN_E2E_REPORT_DIR:-"$repo/.local/test-results/$project"}
mkdir -p "$JIDAN_E2E_REPORT_DIR"
JIDAN_E2E_REPORT_DIR=$(cd "$JIDAN_E2E_REPORT_DIR" && pwd)
export JIDAN_E2E_REPORT_DIR
compose() { docker compose -p "$project" -f compose.yml --profile e2e "$@"; }
child_pid=
run_compose() {
  docker compose -p "$project" -f compose.yml --profile e2e "$@" &
  child_pid=$!
  wait "$child_pid"
  child_pid=
}
interrupted() {
  # wait is interruptible; stop the Docker CLI before cleaning up its containers.
  if [ -n "$child_pid" ]; then kill -TERM "$child_pid" 2>/dev/null || true; fi
  exit "$1"
}
cleanup() {
  result=$?
  trap - EXIT INT TERM
  compose logs --no-color e2e-api e2e-mysql > "$JIDAN_E2E_REPORT_DIR/services.log" 2>&1 || true
  if [ "$result" -ne 0 ]; then cat "$JIDAN_E2E_REPORT_DIR/services.log"; fi
  if ! compose down --volumes --remove-orphans --rmi local; then
    if [ "$result" -eq 0 ]; then result=1; fi
  fi
  printf 'Test reports: %s\n' "$JIDAN_E2E_REPORT_DIR"
  exit "$result"
}
trap cleanup EXIT
trap 'interrupted 130' INT
trap 'interrupted 143' TERM
run_compose up --detach --wait --wait-timeout 180 e2e-mysql
run_compose run --build --rm --no-deps checks
# Schema-resetting Python tests finish before the API/background jobs start.
run_compose up --build --detach --wait --wait-timeout 180 e2e-api
run_compose run --build --rm --no-deps e2e
