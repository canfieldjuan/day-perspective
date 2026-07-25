#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
local_root="$project_root/.local"

case "$local_root" in
  "$project_root"/.local) ;;
  *)
    printf 'Refusing to clean an unexpected local storage path: %s\n' "$local_root" >&2
    exit 1
    ;;
esac

if [[ -d "$local_root" ]]; then
  find "$local_root" -mindepth 1 -depth -delete
fi

printf 'Reset local raw-source and published-profile storage under %s\n' "$local_root"
