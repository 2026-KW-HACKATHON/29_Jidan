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
cleanup() {
  result=$?
  trap - EXIT INT TERM
  compose logs --no-color e2e-api e2e-mysql > "$JIDAN_E2E_REPORT_DIR/services.log" 2>&1 || true
  if [ "$result" -ne 0 ]; then cat "$JIDAN_E2E_REPORT_DIR/services.log"; fi
  if ! compose down --volumes --remove-orphans; then
    if [ "$result" -eq 0 ]; then result=1; fi
  fi
  printf 'Test reports: %s\n' "$JIDAN_E2E_REPORT_DIR"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose up --build --detach --wait --wait-timeout 180 e2e-api
compose run --build --rm --no-deps e2e
