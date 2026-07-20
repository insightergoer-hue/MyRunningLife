#!/usr/bin/env python3
"""Pull Garmin Connect daily summary + activities into plans/garmin/daily/.

Usage (from repo root or this directory):
  python3 scripts/garmin/sync_garmin.py              # yesterday (Asia/Shanghai)
  python3 scripts/garmin/sync_garmin.py --date 2026-07-16
  python3 scripts/garmin/sync_garmin.py --days 3     # last 3 days through yesterday
  python3 scripts/garmin/sync_garmin.py --today

First run may prompt for MFA code. Tokens are cached in GARMINTOKENS.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from garminconnect import Garmin
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing garminconnect. Run: pip install -r scripts/garmin/requirements.txt"
    ) from exc

TZ = ZoneInfo("Asia/Shanghai")
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
OUT_DIR = REPO_ROOT / "plans" / "garmin" / "daily"
RAW_DIR = REPO_ROOT / "plans" / "garmin" / "raw"


def _load_env() -> None:
    if load_dotenv:
        load_dotenv(SCRIPT_DIR / ".env")
        load_dotenv(REPO_ROOT / ".env")


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — keep sync resilient
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _dig(data: Any, *keys: str, default: Any = None) -> Any:
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None or v == "" or (isinstance(v, dict) and "_error" in v):
        return "—"
    return f"{v}{suffix}"


def login_client() -> Garmin:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    is_cn = _bool_env("GARMIN_IS_CN", False)
    tokenstore = os.getenv("GARMINTOKENS", str(Path("~/.garminconnect_zhangchaojie").expanduser()))

    client = Garmin(email=email, password=password, is_cn=is_cn)
    try:
        client.login(tokenstore=tokenstore)
        return client
    except Exception:
        if not email or not password:
            raise SystemExit(
                "Login failed and GARMIN_EMAIL/GARMIN_PASSWORD not set. "
                "Copy scripts/garmin/.env.example → .env and fill credentials."
            )
        client = Garmin(email=email, password=password, is_cn=is_cn)
        result = client.login(tokenstore=tokenstore)
        # Persist tokens for next runs (garminconnect writes via garth when path given)
        if result and result[0]:
            raise SystemExit(
                "MFA required. Re-run interactively in a terminal so you can enter the MFA code, "
                "then tokens will be cached."
            )
        return client


def collect_day(client: Garmin, day: date) -> dict[str, Any]:
    ds = day.isoformat()
    payload: dict[str, Any] = {
        "date": ds,
        "synced_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "stats": _safe_call(client.get_stats, ds),
        "sleep": _safe_call(client.get_sleep_data, ds),
        "hrv": _safe_call(client.get_hrv_data, ds),
        "stress": _safe_call(client.get_stress_data, ds),
        "body_battery": _safe_call(client.get_body_battery, ds),
        "heart_rates": _safe_call(client.get_heart_rates, ds),
        "training_readiness": _safe_call(client.get_training_readiness, ds),
        "training_status": _safe_call(client.get_training_status, ds),
        "steps": _safe_call(client.get_daily_steps, ds, ds),
        "activities": _safe_call(client.get_activities_by_date, ds, ds),
    }
    # Enrich each activity with TE / zones when possible
    acts = payload.get("activities")
    if isinstance(acts, list):
        enriched = []
        for act in acts:
            aid = act.get("activityId")
            item = {"summary": act}
            if aid is not None:
                item["hr_zones"] = _safe_call(client.get_activity_hr_in_timezones, aid)
                item["details"] = _safe_call(client.get_activity, aid)
            enriched.append(item)
        payload["activities"] = enriched
    return payload


def _sleep_summary(sleep: Any) -> dict[str, Any]:
    if not isinstance(sleep, dict) or "_error" in sleep:
        return {}
    dto = sleep.get("dailySleepDTO") or sleep.get("sleepDTO") or {}
    scores = sleep.get("sleepScores") or {}
    overall = _dig(scores, "overall", "value") or sleep.get("sleepScore")
    return {
        "score": overall,
        "total_sleep_s": dto.get("sleepTimeSeconds"),
        "deep_s": dto.get("deepSleepSeconds"),
        "light_s": dto.get("lightSleepSeconds"),
        "rem_s": dto.get("remSleepSeconds"),
        "awake_s": dto.get("awakeSleepSeconds"),
        "avg_rr": dto.get("averageRespirationValue"),
        "avg_spo2": dto.get("averageSpO2Value"),
    }


def _sec_to_hm(sec: Any) -> str:
    if not isinstance(sec, (int, float)):
        return "—"
    sec = int(sec)
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def _activity_lines(activities: Any) -> list[str]:
    if not isinstance(activities, list) or not activities:
        return ["（无运动记录）"]
    lines: list[str] = []
    for i, item in enumerate(activities, 1):
        s = item.get("summary") if isinstance(item, dict) else item
        if not isinstance(s, dict):
            continue
        dist_m = s.get("distance")
        dist_km = round(dist_m / 1000, 2) if isinstance(dist_m, (int, float)) else None
        dur_s = s.get("duration") or s.get("elapsedDuration")
        dur = _sec_to_hm(dur_s) if dur_s else "—"
        avg_hr = s.get("averageHR")
        max_hr = s.get("maxHR")
        elev = s.get("elevationGain")
        cal = s.get("calories")
        name = s.get("activityName") or s.get("activityType", {}).get("typeKey") or "activity"
        start = s.get("startTimeLocal") or s.get("startTimeGMT") or ""
        # TE often lives in details
        details = item.get("details") if isinstance(item, dict) else None
        aerobic = anaerobic = None
        if isinstance(details, dict) and "_error" not in details:
            aerobic = details.get("aerobicTrainingEffect") or _dig(
                details, "summaryDTO", "aerobicTrainingEffect"
            )
            anaerobic = details.get("anaerobicTrainingEffect") or _dig(
                details, "summaryDTO", "anaerobicTrainingEffect"
            )
            if avg_hr is None:
                avg_hr = details.get("averageHR") or _dig(details, "summaryDTO", "averageHR")
            if max_hr is None:
                max_hr = details.get("maxHR") or _dig(details, "summaryDTO", "maxHR")
        lines.append(
            f"### 活动 {i} · {name}\n"
            f"- 开始：{start}\n"
            f"- 距离：{_fmt(dist_km, ' km')} · 时长：{dur}\n"
            f"- 心率：均 {_fmt(avg_hr)} · 最大 {_fmt(max_hr)}\n"
            f"- 爬升：{_fmt(elev, ' m')} · 卡路里：{_fmt(cal)}\n"
            f"- 有氧 TE：{_fmt(aerobic)} · 无氧 TE：{_fmt(anaerobic)}\n"
        )
    return lines or ["（无运动记录）"]


def render_markdown(payload: dict[str, Any]) -> str:
    day = payload["date"]
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    sleep = _sleep_summary(payload.get("sleep"))
    hrv = payload.get("hrv") if isinstance(payload.get("hrv"), dict) else {}
    readiness = payload.get("training_readiness")
    body_battery = payload.get("body_battery")

    rhr = stats.get("restingHeartRate") if isinstance(stats, dict) else None
    bb_high = bb_low = None
    if isinstance(body_battery, list) and body_battery:
        # list of day values
        values = []
        for row in body_battery:
            if isinstance(row, dict):
                values.extend(
                    [
                        row.get("charged"),
                        row.get("drained"),
                        row.get("bodyBatteryValue"),
                    ]
                )
                vals = row.get("bodyBatteryValuesArray") or row.get("bodyBatteryValueList")
                if isinstance(vals, list):
                    for v in vals:
                        if isinstance(v, (list, tuple)) and len(v) >= 2:
                            values.append(v[1])
                        elif isinstance(v, (int, float)):
                            values.append(v)
        nums = [v for v in values if isinstance(v, (int, float))]
        if nums:
            bb_high, bb_low = max(nums), min(nums)
    elif isinstance(body_battery, dict) and "_error" not in body_battery:
        bb_high = body_battery.get("bodyBatteryMostRecentValue") or body_battery.get(
            "highestBodyBatteryValue"
        )
        bb_low = body_battery.get("lowestBodyBatteryValue")

    hrv_status = _dig(hrv, "hrvSummary", "status") or hrv.get("status")
    hrv_last = _dig(hrv, "hrvSummary", "lastNightAvg") or hrv.get("lastNightAvg")
    hrv_baseline = _dig(hrv, "hrvSummary", "baseline", "balancedLow")

    readiness_score = None
    recovery_time = None
    if isinstance(readiness, list) and readiness:
        latest = readiness[0] if isinstance(readiness[0], dict) else {}
        readiness_score = latest.get("score")
        recovery_time = latest.get("recoveryTime")
    elif isinstance(readiness, dict) and "_error" not in readiness:
        readiness_score = readiness.get("score")
        recovery_time = readiness.get("recoveryTime")

    lines = [
        f"# Garmin 日摘要 · {day}",
        "",
        f"> 自动同步于 {payload.get('synced_at')}（Asia/Shanghai）  ",
        f"> 原始 JSON：`../raw/{day}.json`",
        "",
        "## 恢复 / 身体",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 静息心率 | {_fmt(rhr, ' bpm')} |",
        f"| 睡眠分数 | {_fmt(sleep.get('score'))} |",
        f"| 总睡眠 | {_sec_to_hm(sleep.get('total_sleep_s'))} |",
        f"| 深睡 / 浅睡 / REM / 清醒 | {_sec_to_hm(sleep.get('deep_s'))} / {_sec_to_hm(sleep.get('light_s'))} / {_sec_to_hm(sleep.get('rem_s'))} / {_sec_to_hm(sleep.get('awake_s'))} |",
        f"| HRV（昨夜均） | {_fmt(hrv_last)} · 状态 {_fmt(hrv_status)} |",
        f"| Body Battery（高/低） | {_fmt(bb_high)} / {_fmt(bb_low)} |",
        f"| Training Readiness | {_fmt(readiness_score)} |",
        f"| 恢复时间（表显） | {_fmt(recovery_time)} |",
        "",
        "## 运动",
        "",
    ]
    lines.extend(_activity_lines(payload.get("activities")))
    lines.extend(
        [
            "",
            "## 教练备注栏（可选手写）",
            "",
            "- 主观疲劳（1好–10差）：",
            "- 膝外侧 / 右踝前：",
            "- 其他：",
            "",
        ]
    )
    return "\n".join(lines)


def write_day(payload: dict[str, Any]) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    day = payload["date"]
    raw_path = RAW_DIR / f"{day}.json"
    md_path = OUT_DIR / f"{day}.md"
    raw_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return md_path, raw_path


def git_auto_push(paths: list[Path]) -> None:
    if not _bool_env("GARMIN_GIT_AUTO_PUSH", False):
        return
    rels = [str(p.relative_to(REPO_ROOT)) for p in paths]
    subprocess.run(["git", "add", *rels], cwd=REPO_ROOT, check=True)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *rels],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if not status.stdout.strip():
        print("Git: nothing new to commit.")
        return
    days = ", ".join(sorted({p.stem for p in paths if p.suffix == ".md"}))
    msg = f"Sync Garmin daily data ({days})"
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=REPO_ROOT, check=True)
    print("Git: committed and pushed.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync Garmin Connect → plans/garmin/")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", help="YYYY-MM-DD (Asia/Shanghai calendar day)")
    g.add_argument("--today", action="store_true", help="Sync today")
    g.add_argument("--days", type=int, help="Sync last N days through yesterday")
    return p.parse_args()


def target_days(args: argparse.Namespace) -> list[date]:
    today = datetime.now(TZ).date()
    if args.date:
        return [date.fromisoformat(args.date)]
    if args.today:
        return [today]
    if args.days:
        n = max(1, args.days)
        end = today - timedelta(days=1)
        return [end - timedelta(days=i) for i in range(n - 1, -1, -1)]
    return [today - timedelta(days=1)]


def main() -> int:
    _load_env()
    args = parse_args()
    days = target_days(args)
    print(f"Repo: {REPO_ROOT}")
    print(f"Days: {', '.join(d.isoformat() for d in days)}")
    client = login_client()
    written: list[Path] = []
    for day in days:
        print(f"Fetching {day} …")
        payload = collect_day(client, day)
        md_path, raw_path = write_day(payload)
        written.extend([md_path, raw_path])
        print(f"  → {md_path.relative_to(REPO_ROOT)}")
        print(f"  → {raw_path.relative_to(REPO_ROOT)}")
    git_auto_push(written)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
