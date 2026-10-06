#!/bin/sh
# Every invocation gets its own project. Cleanup never targets the manual sandbox.
set -eu
cd "$(dirname "$0")"
project="jidan-e2e-$(date +%s)-$$"
compose() { docker compose -p "$project" -f compose.yml --profile e2e "$@"; }
cleanup() {
  result=$?
  trap - EXIT INT TERM
  if [ "$result" -ne 0 ]; then compose logs --no-color e2e-api e2e-mysql || true; fi
  compose down --volumes --remove-orphans || true
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose up --build --detach --wait --wait-timeout 180 e2e-api
compose run --build --rm --no-deps e2e
