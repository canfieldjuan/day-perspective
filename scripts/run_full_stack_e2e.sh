#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${API_PYTHON:-$project_root/.venv/bin/python}"
database_url="${DATABASE_URL:-postgresql+psycopg://day_perspective:day_perspective@localhost:54329/day_perspective}"
published_root="${PUBLISHED_PROFILE_ROOT:-$project_root/.local/published-profiles}"
api_port="${FULL_STACK_API_PORT:-18080}"

cd "$project_root/services/api"
DATABASE_URL="$database_url" PUBLISHED_PROFILE_ROOT="$published_root" \
  "$python_bin" -m uvicorn app.main:app --host 127.0.0.1 --port "$api_port" \
  >"$project_root/.local/full-stack-api.log" 2>&1 &
api_pid=$!

cleanup() {
  kill "$api_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 80); do
  if curl -fsS "http://127.0.0.1:$api_port/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done

if [[ "${ready:-0}" != "1" ]]; then
  cat "$project_root/.local/full-stack-api.log" >&2
  exit 1
fi

cd "$project_root/apps/web"
DAY_PERSPECTIVE_FULL_STACK=1 \
API_BASE_URL="http://127.0.0.1:$api_port" \
  corepack pnpm exec playwright test e2e/full-stack-golden.spec.ts
