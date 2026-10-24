#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -d "$PROJECT_ROOT/.git-local/objects" ]]; then
    exec git -C "$PROJECT_ROOT" --git-dir="$PROJECT_ROOT/.git-local" --work-tree="$PROJECT_ROOT" "$@"
fi
exec git -C "$PROJECT_ROOT" "$@"
