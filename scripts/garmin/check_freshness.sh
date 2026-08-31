#!/usr/bin/env bash
# 检查 plans/garmin/daily/YYYY-MM-DD.md 是否为「今天（北京）」且同步时间足够新。
# 用于本地或 CI 冒烟；watchdog 已改为强制同步，此脚本供人工排查。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DAY="${1:-$(TZ=Asia/Shanghai date +%F)}"
FILE="${REPO_ROOT}/plans/garmin/daily/${DAY}.md"
MIN_HOUR="${2:-8}" # 北京时间，期望 sync 不早于该小时（提醒前应有当日数据）

if [[ ! -f "$FILE" ]]; then
  echo "STALE: missing ${FILE}"
  exit 1
fi

LINE=$(grep -m1 '自动同步于' "$FILE" || true)
if [[ -z "$LINE" ]]; then
  echo "STALE: no synced_at in ${FILE}"
  exit 1
fi

# 例：> 自动同步于 2026-08-31T17:42:33+08:00（Asia/Shanghai）
SYNC_TS=$(echo "$LINE" | sed -n 's/.*自动同步于 \([0-9T:+-]*\).*/\1/p')
SYNC_DAY=$(echo "$SYNC_TS" | cut -c1-10)
SYNC_HOUR=$(echo "$SYNC_TS" | sed -n 's/.*T\([0-9][0-9]\):.*/\1/p')

if [[ "$SYNC_DAY" != "$DAY" ]]; then
  echo "STALE: file day ${DAY} but synced_at day ${SYNC_DAY}"
  exit 1
fi

if [[ -n "$SYNC_HOUR" && "$SYNC_HOUR" -lt "$MIN_HOUR" ]]; then
  echo "STALE: synced at hour ${SYNC_HOUR} Beijing (< ${MIN_HOUR})"
  exit 1
fi

echo "OK: ${FILE} synced_at ${SYNC_TS}"
exit 0
