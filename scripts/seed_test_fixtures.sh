#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${API_PYTHON:-$project_root/.venv/bin/python}"

cd "$project_root/services/api"
exec "$python_bin" -m app.fixtures --confirm-test-fixtures
