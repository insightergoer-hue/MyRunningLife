#!/usr/bin/env bash
# Convenience wrapper: sync yesterday and optionally push.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python3 scripts/garmin/sync_garmin.py "$@"
