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
    # Enrich each activity with Connect detail endpoints (TE / zones / weather / gear / splits)
    acts = payload.get("activities")
    if isinstance(acts, list):
        enriched = []
        for act in acts:
            aid = act.get("activityId")
            item: dict[str, Any] = {"summary": act}
            if aid is not None:
                item["hr_zones"] = _safe_call(client.get_activity_hr_in_timezones, aid)
                item["power_zones"] = _safe_call(client.get_activity_power_in_timezones, aid)
                item["details"] = _safe_call(client.get_activity, aid)
                item["weather"] = _safe_call(client.get_activity_weather, aid)
                item["gear"] = _safe_call(client.get_activity_gear, aid)
                item["splits"] = _safe_call(client.get_activity_splits, aid)
                item["split_summaries"] = _safe_call(client.get_activity_split_summaries, aid)
                item["typed_splits"] = _safe_call(client.get_activity_typed_splits, aid)
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


def _sec_to_ms(sec: Any) -> str:
    """Format seconds as m:ss (pace)."""
    if not isinstance(sec, (int, float)) or sec <= 0:
        return "—"
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _speed_to_pace(speed_mps: Any) -> str:
    """m/s → min/km pace string."""
    if not isinstance(speed_mps, (int, float)) or speed_mps <= 0:
        return "—"
    return _sec_to_ms(1000.0 / speed_mps)


def _round_num(v: Any, ndigits: int = 1) -> Any:
    if isinstance(v, (int, float)):
        r = round(float(v), ndigits)
        return int(r) if ndigits == 0 else r
    return v


def _first(*vals: Any) -> Any:
    for v in vals:
        if v is None or v == "":
            continue
        if isinstance(v, dict) and "_error" in v:
            continue
        return v
    return None


# Keys already rendered in structured sections — leftover dump skips these + noise.
_ACTIVITY_RENDERED_KEYS = {
    "activityId",
    "activityName",
    "activityType",
    "activityTrainingLoad",
    "aerobicTrainingEffect",
    "aerobicTrainingEffectMessage",
    "anaerobicTrainingEffect",
    "anaerobicTrainingEffectMessage",
    "averageHR",
    "averageMovingSpeed",
    "averagePower",
    "averageRunCadence",
    "averageRunningCadenceInStepsPerMinute",
    "averageSpeed",
    "avgElevation",
    "avgGradeAdjustedSpeed",
    "avgGroundContactTime",
    "avgPower",
    "avgStrideLength",
    "avgVerticalOscillation",
    "avgVerticalRatio",
    "beginPotentialStamina",
    "beginTimestamp",
    "bmrCalories",
    "calories",
    "differenceBodyBattery",
    "directWorkoutComplianceScore",
    "directWorkoutFeel",
    "directWorkoutRpe",
    "distance",
    "duration",
    "elapsedDuration",
    "elevationGain",
    "elevationLoss",
    "endLatitude",
    "endLongitude",
    "endPotentialStamina",
    "endTimeGMT",
    "eventType",
    "fastestSplit_1000",
    "fastestSplit_10000",
    "fastestSplit_1609",
    "fastestSplit_5000",
    "groundContactTime",
    "hrTimeInZone_1",
    "hrTimeInZone_2",
    "hrTimeInZone_3",
    "hrTimeInZone_4",
    "hrTimeInZone_5",
    "lapCount",
    "locationName",
    "manufacturer",
    "maxDoubleCadence",
    "maxElevation",
    "maxHR",
    "maxPower",
    "maxRunCadence",
    "maxRunningCadenceInStepsPerMinute",
    "maxSpeed",
    "maxVerticalSpeed",
    "minActivityLapDuration",
    "minAvailableStamina",
    "minElevation",
    "minHR",
    "minPower",
    "moderateIntensityMinutes",
    "movingDuration",
    "normPower",
    "normalizedPower",
    "powerTimeInZone_1",
    "powerTimeInZone_2",
    "powerTimeInZone_3",
    "powerTimeInZone_4",
    "powerTimeInZone_5",
    "sportTypeId",
    "startLatitude",
    "startLongitude",
    "startTimeGMT",
    "startTimeLocal",
    "steps",
    "strideLength",
    "totalWork",
    "trainingEffect",
    "trainingEffectLabel",
    "vO2MaxValue",
    "verticalOscillation",
    "verticalRatio",
    "vigorousIntensityMinutes",
    "waterEstimated",
    "workoutId",
    "courseId",
    "deviceId",
    "splitSummaries",
}

_ACTIVITY_NOISE_KEYS = {
    "activityUUID",
    "atpActivity",
    "autoCalcCalories",
    "decoDive",
    "elevationCorrected",
    "favorite",
    "hasHeatMap",
    "hasImages",
    "hasIntensityIntervals",
    "hasPolyline",
    "hasSplits",
    "hasVideo",
    "manualActivity",
    "ownerDisplayName",
    "ownerFullName",
    "ownerId",
    "ownerProfileImageUrlLarge",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlSmall",
    "parent",
    "pr",
    "privacy",
    "purposeful",
    "qualifyingDive",
    "summarizedDiveInfo",
    "timeZoneId",
    "userPro",
    "userRoles",
}


def _merge_activity(item: Any) -> dict[str, Any]:
    """Merge list-summary + details.summaryDTO; list summary wins on conflict."""
    if not isinstance(item, dict):
        return {}
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    dto = details.get("summaryDTO") if isinstance(details.get("summaryDTO"), dict) else {}
    merged: dict[str, Any] = {}
    for src in (dto, summary):
        for k, v in src.items():
            if v is not None:
                merged[k] = v
    if merged.get("aerobicTrainingEffect") is None and merged.get("trainingEffect") is not None:
        merged["aerobicTrainingEffect"] = merged["trainingEffect"]
    if merged.get("averageRunningCadenceInStepsPerMinute") is None and merged.get(
        "averageRunCadence"
    ) is not None:
        merged["averageRunningCadenceInStepsPerMinute"] = merged["averageRunCadence"]
    if merged.get("maxRunningCadenceInStepsPerMinute") is None and merged.get(
        "maxRunCadence"
    ) is not None:
        merged["maxRunningCadenceInStepsPerMinute"] = merged["maxRunCadence"]
    if merged.get("avgStrideLength") is None and merged.get("strideLength") is not None:
        merged["avgStrideLength"] = merged["strideLength"]
    if merged.get("avgGroundContactTime") is None and merged.get("groundContactTime") is not None:
        merged["avgGroundContactTime"] = merged["groundContactTime"]
    if merged.get("avgVerticalOscillation") is None and merged.get("verticalOscillation") is not None:
        merged["avgVerticalOscillation"] = merged["verticalOscillation"]
    if merged.get("avgVerticalRatio") is None and merged.get("verticalRatio") is not None:
        merged["avgVerticalRatio"] = merged["verticalRatio"]
    if merged.get("normalizedPower") is None and merged.get("normPower") is not None:
        merged["normalizedPower"] = merged["normPower"]
    return merged


def _zone_line_from_list(zones: Any, label_prefix: str = "Z") -> str:
    if not isinstance(zones, list) or not zones:
        return ""
    parts: list[str] = []
    for z in zones:
        if not isinstance(z, dict):
            continue
        n = z.get("zoneNumber")
        secs = z.get("secsInZone")
        if n is None or not isinstance(secs, (int, float)) or secs < 1:
            continue
        lo = z.get("zoneLowBoundary")
        bound = f"（≥{lo}）" if lo is not None else ""
        parts.append(f"{label_prefix}{n}{bound} {_sec_to_hm(secs)}")
    return " · ".join(parts)


def _zone_line_from_summary(s: dict[str, Any], prefix: str) -> str:
    parts: list[str] = []
    for n in range(1, 6):
        secs = s.get(f"{prefix}{n}")
        if isinstance(secs, (int, float)) and secs >= 1:
            parts.append(f"Z{n} {_sec_to_hm(secs)}")
    return " · ".join(parts)


def _te_label_zh(label: Any) -> str:
    if not label:
        return ""
    mapping = {
        "OVERREACHING": "过度训练",
        "AEROBIC_BASE": "有氧基础",
        "AEROBIC_TEMPO": "有氧节奏",
        "LACTATE_THRESHOLD": "乳酸阈值",
        "VO2_MAX": "VO2max",
        "ANAEROBIC_CAPACITY": "无氧能力",
        "RECOVERY": "恢复",
    }
    return mapping.get(str(label), str(label))


def _te_message_zh(msg: Any) -> str:
    if not msg:
        return ""
    text = str(msg)
    # Strip trailing _N codes Garmin appends
    if "_" in text and text.rsplit("_", 1)[-1].isdigit():
        text = text.rsplit("_", 1)[0]
    return text.replace("_", " ")


def _kv_line(label: str, value: str) -> str:
    return f"- {label}：{value}"


def _f_to_c(temp_f: Any) -> Any:
    if not isinstance(temp_f, (int, float)):
        return None
    return round((temp_f - 32) * 5 / 9, 1)


def _mph_to_kmh(mph: Any) -> Any:
    if not isinstance(mph, (int, float)):
        return None
    return round(mph * 1.60934, 1)


def _weather_lines(weather: Any) -> list[str]:
    if not isinstance(weather, dict) or "_error" in weather:
        return []
    w = weather.get("weatherDTO") if isinstance(weather.get("weatherDTO"), dict) else weather
    wtype = w.get("weatherTypeDTO") if isinstance(w.get("weatherTypeDTO"), dict) else {}
    temp_f = _first(w.get("temp"), w.get("temperature"))
    app_f = _first(w.get("apparentTemp"), w.get("apparentTemperature"), w.get("windChill"))
    dew_f = w.get("dewPoint")
    humidity = _first(w.get("relativeHumidity"), w.get("humidity"))
    wind_mph = _first(w.get("windSpeed"), w.get("wind"))
    gust_mph = w.get("windGust")
    wind_dir = _first(w.get("windDirectionCompassPoint"), w.get("windDirection"))
    cond = _first(wtype.get("desc"), w.get("condition"), w.get("desc"))

    parts: list[str] = []
    if temp_f is not None:
        parts.append(f"气温 {_f_to_c(temp_f)}°C（{temp_f}°F）")
    if app_f is not None:
        parts.append(f"体感 {_f_to_c(app_f)}°C")
    if dew_f is not None:
        parts.append(f"露点 {_f_to_c(dew_f)}°C")
    if humidity is not None:
        parts.append(f"湿度 {_fmt(humidity)}%")
    if cond:
        parts.append(str(cond))
    if wind_mph is not None:
        wind_s = f"风 {_mph_to_kmh(wind_mph)} km/h"
        if wind_dir is not None:
            wind_s += f" {wind_dir}"
        if gust_mph is not None:
            wind_s += f"（阵风 {_mph_to_kmh(gust_mph)}）"
        parts.append(wind_s)
    rows: list[str] = []
    if parts:
        rows.append(_kv_line("天气", " · ".join(parts)))
    return rows


def _gear_lines(gear: Any) -> list[str]:
    if gear is None or (isinstance(gear, dict) and "_error" in gear):
        return []
    items = gear if isinstance(gear, list) else [gear]
    rows: list[str] = []
    for g in items:
        if not isinstance(g, dict):
            continue
        name = _first(g.get("displayName"), g.get("customMakeModel"), g.get("gearPk"))
        gtype = _dig(g, "gearTypeName") or g.get("gearTypePk")
        dist = g.get("totalDistance")
        dist_km = round(dist / 1000, 1) if isinstance(dist, (int, float)) else None
        bits = [str(name)]
        if gtype:
            bits.append(str(gtype))
        if dist_km is not None:
            bits.append(f"累计 {dist_km} km")
        rows.append(_kv_line("装备", " · ".join(bits)))
    return rows


def _sec_to_hms(sec: Any) -> str:
    """Format seconds as H:MM:SS (Garmin-style)."""
    if not isinstance(sec, (int, float)):
        return "—"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _feel_zh(feel: Any) -> str:
    """Garmin directWorkoutFeel → 中文（与 Connect「感觉如何」一致）。"""
    if feel is None:
        return "—"
    try:
        v = int(feel)
    except (TypeError, ValueError):
        return str(feel)
    mapping = {
        0: "很轻松",
        25: "轻松",
        50: "稍微累",
        75: "累",
        100: "很累",
    }
    if v in mapping:
        return mapping[v]
    # nearest bucket
    nearest = min(mapping, key=lambda k: abs(k - v))
    return f"{mapping[nearest]}（{v}）"


def _rpe_zh(rpe: Any) -> str:
    """Garmin directWorkoutRpe → x/10（API 用 10/20/… 表示 1/2/…）。"""
    if rpe is None:
        return "—"
    try:
        v = float(rpe)
    except (TypeError, ValueError):
        return str(rpe)
    # Connect shows 1–10; API stores 10, 20, … 100
    score = v / 10.0 if v >= 10 else v
    score_i = int(round(score))
    labels = {
        1: "很轻松",
        2: "轻松",
        3: "适中",
        4: "有点吃力",
        5: "吃力",
        6: "很吃力",
        7: "非常吃力",
        8: "极限附近",
        9: "接近力竭",
        10: "力竭",
    }
    return f"{score_i}/10 {labels.get(score_i, '')}".strip()


def _aerobic_msg_zh(msg: Any) -> str:
    if not msg:
        return ""
    text = str(msg)
    if "_" in text and text.rsplit("_", 1)[-1].isdigit():
        text = text.rsplit("_", 1)[0]
    mapping = {
        "IMPROVING_AEROBIC_BASE": "有所提高",
        "MAINTAINING_AEROBIC_BASE": "有所维持",
        "IMPROVING_AEROBIC_CAPACITY": "有所提高",
        "MAINTAINING_AEROBIC_CAPACITY": "有所维持",
        "HIGHLY_IMPROVING_AEROBIC_BASE": "显著提高",
        "RECOVERY": "恢复",
        "NO_AEROBIC_BENEFIT": "无效益",
        "NO_ANAEROBIC_BENEFIT": "无效益",
        "IMPROVING_ANAEROBIC_CAPACITY": "有所提高",
        "MAINTAINING_ANAEROBIC_CAPACITY": "有所维持",
    }
    return mapping.get(text, text.replace("_", " "))


def _run_walk_idle_line(split_summaries: Any) -> str:
    """Extract 跑步/走路/静止 from RWD_* split summaries."""
    if not isinstance(split_summaries, list):
        return ""
    want = {
        "RWD_RUN": "跑步",
        "RWD_WALK": "走路",
        "RWD_STAND": "静止",
    }
    parts: list[str] = []
    for sp in split_summaries:
        if not isinstance(sp, dict):
            continue
        st = sp.get("splitType")
        if st not in want:
            continue
        parts.append(f"{want[st]} {_sec_to_hms(sp.get('duration'))}")
    return " · ".join(parts)


def _km_split_lines(splits_payload: Any) -> list[str]:
    """Render per-km laps (skip tiny scrap <50m)."""
    if isinstance(splits_payload, dict) and "_error" in splits_payload:
        return []
    lap_list: list[Any] = []
    if isinstance(splits_payload, list):
        lap_list = splits_payload
    elif isinstance(splits_payload, dict):
        for key in ("lapDTOs", "splits", "splitDTOs"):
            if isinstance(splits_payload.get(key), list):
                lap_list = splits_payload[key]
                break
    if not lap_list:
        return []
    rows = ["- **每公里配速**"]
    rows.append("  | km | 配速 | 心率 | 步频 | 爬升 |")
    rows.append("  |----|------|------|------|------|")
    km_i = 0
    for lap in lap_list:
        if not isinstance(lap, dict):
            continue
        dist = _first(lap.get("distance"), lap.get("distanceInMeters"))
        if not isinstance(dist, (int, float)) or dist < 50:
            continue
        km_i += 1
        pace = _speed_to_pace(
            _first(lap.get("averageMovingSpeed"), lap.get("averageSpeed"), lap.get("speed"))
        )
        hr = _fmt(_round_num(_first(lap.get("averageHR"), lap.get("avgHR")), 0))
        cad = _fmt(
            _round_num(
                _first(
                    lap.get("averageRunCadence"),
                    lap.get("averageCadence"),
                    lap.get("avgRunCadence"),
                ),
                0,
            )
        )
        elev = _fmt(
            _round_num(_first(lap.get("elevationGain"), lap.get("elevGain")), 0),
            " m",
        )
        label = f"{km_i}" if dist >= 950 else f"{km_i}（{round(dist)}m）"
        rows.append(f"  | {label} | {pace} | {hr} | {cad} | {elev} |")
    if km_i == 0:
        return []
    return rows


def _weather_brief(weather: Any) -> str:
    lines = _weather_lines(weather)
    if not lines:
        return ""
    # strip leading "- 天气："
    return lines[0].split("：", 1)[-1] if "：" in lines[0] else lines[0]


def _activity_lines(activities: Any) -> list[str]:
    """Render coaching-useful activity fields (aligned with Connect 统计信息)."""
    if not isinstance(activities, list) or not activities:
        return ["（无运动记录）"]
    lines: list[str] = []
    for i, item in enumerate(activities, 1):
        if not isinstance(item, dict):
            continue
        s = _merge_activity(item)
        if not s:
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        aid = s.get("activityId") or details.get("activityId")
        name = (
            s.get("activityName")
            or _dig(s, "activityType", "typeKey")
            or details.get("activityName")
            or "activity"
        )
        start = s.get("startTimeLocal") or s.get("startTimeGMT") or ""
        dist_m = s.get("distance")
        dist_km = round(dist_m / 1000, 2) if isinstance(dist_m, (int, float)) else None
        dur_s = _first(s.get("duration"), s.get("elapsedDuration"))
        elapsed_s = s.get("elapsedDuration")
        moving_s = s.get("movingDuration")
        avg_pace = _speed_to_pace(s.get("averageSpeed"))
        moving_pace = _speed_to_pace(
            _first(s.get("averageMovingSpeed"), s.get("averageSpeed"))
        )
        best_pace = _speed_to_pace(s.get("maxSpeed"))
        gas_pace = _speed_to_pace(s.get("avgGradeAdjustedSpeed"))
        avg_cad = _first(
            s.get("averageRunningCadenceInStepsPerMinute"), s.get("averageRunCadence")
        )
        max_cad = _first(
            s.get("maxRunningCadenceInStepsPerMinute"),
            s.get("maxRunCadence"),
            s.get("maxDoubleCadence"),
        )
        stride_cm = _first(s.get("avgStrideLength"), s.get("strideLength"))
        stride_m = round(stride_cm / 100, 2) if isinstance(stride_cm, (int, float)) else None
        gct = _first(s.get("avgGroundContactTime"), s.get("groundContactTime"))
        vert_osc = _first(s.get("avgVerticalOscillation"), s.get("verticalOscillation"))
        vert_ratio = _first(s.get("avgVerticalRatio"), s.get("verticalRatio"))
        aerobic = _first(s.get("aerobicTrainingEffect"), s.get("trainingEffect"))
        anaerobic = s.get("anaerobicTrainingEffect")
        te_label = _te_label_zh(s.get("trainingEffectLabel"))
        avg_power = _first(s.get("averagePower"), s.get("avgPower"))
        cal_total = s.get("calories")
        cal_bmr = s.get("bmrCalories")
        cal_active = None
        if isinstance(cal_total, (int, float)) and isinstance(cal_bmr, (int, float)):
            cal_active = cal_total - cal_bmr
        water = s.get("waterEstimated")

        hr_zones = item.get("hr_zones")
        hr_zone_line = _zone_line_from_list(hr_zones) or _zone_line_from_summary(
            s, "hrTimeInZone_"
        )

        split_summaries = None
        for cand in (
            details.get("splitSummaries") if isinstance(details, dict) else None,
            item.get("split_summaries"),
            s.get("splitSummaries"),
        ):
            if isinstance(cand, list) and cand:
                split_summaries = cand
                break
            if isinstance(cand, dict) and "_error" not in cand:
                inner = cand.get("splitSummaries") or cand.get("splitSummaryDTOs")
                if isinstance(inner, list) and inner:
                    split_summaries = inner
                    break
        rwd_line = _run_walk_idle_line(split_summaries)

        stamina_begin = s.get("beginPotentialStamina")
        stamina_end = s.get("endPotentialStamina")
        stamina_min = s.get("minAvailableStamina")

        block: list[str] = [
            f"### 活动 {i} · {name}",
            _kv_line("开始", f"{start} · {_fmt(s.get('locationName'))}"),
            _kv_line(
                "距离 / 时间",
                f"{_fmt(dist_km, ' km')} · 时间 {_sec_to_hms(dur_s)}"
                f" · 移动 {_sec_to_hms(moving_s)} · 全程 {_sec_to_hms(elapsed_s)}",
            ),
            _kv_line(
                "配速",
                f"平均 {_fmt(avg_pace)} /km · 平均移动 {_fmt(moving_pace)} /km"
                f" · 最佳 {_fmt(best_pace)} /km"
                + (f" · 坡度修正 {_fmt(gas_pace)} /km" if gas_pace != "—" else ""),
            ),
            _kv_line(
                "心率",
                f"平均 {_fmt(_round_num(s.get('averageHR'), 0))} bpm · "
                f"最大 {_fmt(_round_num(s.get('maxHR'), 0))} bpm",
            ),
            _kv_line(
                "功率",
                f"平均 {_fmt(_round_num(avg_power, 0))} W · "
                f"最大 {_fmt(_round_num(s.get('maxPower'), 0))} W",
            ),
            _kv_line(
                "训练效果",
                f"主要好处 {_fmt(te_label or s.get('trainingEffectLabel'))} · "
                f"有氧 {_fmt(_round_num(aerobic, 1))}（{_aerobic_msg_zh(s.get('aerobicTrainingEffectMessage'))}） · "
                f"无氧 {_fmt(_round_num(anaerobic, 1))}（{_aerobic_msg_zh(s.get('anaerobicTrainingEffectMessage'))}） · "
                f"运动负荷 {_fmt(_round_num(s.get('activityTrainingLoad'), 0))}",
            ),
            _kv_line(
                "跑步动态",
                f"步频均 {_fmt(_round_num(avg_cad, 0))} / 最高 {_fmt(_round_num(max_cad, 0))} spm · "
                f"步长 {_fmt(stride_m, ' m')} · "
                f"垂直步幅比 {_fmt(_round_num(vert_ratio, 1), '%')} · "
                f"垂直振幅 {_fmt(_round_num(vert_osc, 1), ' cm')} · "
                f"触地 {_fmt(_round_num(gct, 0), ' ms')}",
            ),
            _kv_line(
                "体力 / Battery",
                f"体力 {_fmt(_round_num(stamina_begin, 0))}% → "
                f"{_fmt(_round_num(stamina_end, 0))}%（最低 {_fmt(_round_num(stamina_min, 0))}%） · "
                f"Body Battery {_fmt(s.get('differenceBodyBattery'))}"
                + (
                    f" · VO2max {_fmt(s.get('vO2MaxValue'))}"
                    if s.get("vO2MaxValue") is not None
                    else ""
                ),
            ),
            _kv_line(
                "海拔",
                f"爬升 {_fmt(_round_num(s.get('elevationGain'), 0), ' m')} · "
                f"下降 {_fmt(_round_num(s.get('elevationLoss'), 0), ' m')} · "
                f"最低 {_fmt(_round_num(s.get('minElevation'), 0), ' m')} · "
                f"最高 {_fmt(_round_num(s.get('maxElevation'), 0), ' m')}",
            ),
        ]
        if rwd_line:
            block.append(_kv_line("跑/走/静止", rwd_line))
        block.append(
            _kv_line(
                "热量 / 水分",
                f"总消耗 {_fmt(_round_num(cal_total, 0))}（活动 {_fmt(_round_num(cal_active, 0))} · "
                f"静态 {_fmt(_round_num(cal_bmr, 0))}） · "
                f"预估出汗 {_fmt(_round_num(water, 0), ' ml')}"
                + (
                    f" · 净补水 -{_fmt(_round_num(water, 0), ' ml')}"
                    if isinstance(water, (int, float))
                    else ""
                ),
            )
        )
        # Intensity minutes: keep but note Garmin's HR-based definition often undercounts easy Z2
        mod_m = s.get("moderateIntensityMinutes")
        vig_m = s.get("vigorousIntensityMinutes")
        if mod_m is not None or vig_m is not None:
            block.append(
                _kv_line(
                    "强度分钟（表显）",
                    f"中等 {_fmt(mod_m)} · 剧烈 {_fmt(vig_m)} "
                    f"（Garmin 定义，轻松有氧常偏少，以心率区间为准）",
                )
            )
        block.append(_kv_line("心率区间", hr_zone_line or "—"))
        block.append(
            _kv_line(
                "主观感受",
                f"感觉 {_feel_zh(s.get('directWorkoutFeel'))} · "
                f"感知强度 {_rpe_zh(s.get('directWorkoutRpe'))}",
            )
        )
        # Fastest 1k/5k useful for progress; skip 1mi / junk min lap
        fast_bits = []
        if s.get("fastestSplit_1000") is not None:
            fast_bits.append(f"最快 1 km {_sec_to_ms(s.get('fastestSplit_1000'))}")
        if s.get("fastestSplit_5000") is not None:
            fast_bits.append(f"5 km {_sec_to_ms(s.get('fastestSplit_5000'))}")
        if s.get("fastestSplit_10000") is not None:
            fast_bits.append(f"10 km {_sec_to_ms(s.get('fastestSplit_10000'))}")
        if fast_bits:
            block.append(_kv_line("最快分段", " · ".join(fast_bits)))
        wbrief = _weather_brief(item.get("weather"))
        if wbrief:
            block.append(_kv_line("天气", wbrief))
        block.extend(_gear_lines(item.get("gear")))
        block.extend(_km_split_lines(item.get("splits")))
        if aid is not None:
            block.append(
                _kv_line("Connect", f"https://connect.garmin.cn/app/activity/{aid}")
            )
        lines.append("\n".join(block) + "\n")
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
        f"> 原始 JSON（仅本机，不进 git）：`../raw/{day}.json`",
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
    """Commit/push daily markdown only — never raw JSON."""
    if not _bool_env("GARMIN_GIT_AUTO_PUSH", False):
        return
    md_paths = [p for p in paths if p.suffix == ".md"]
    if not md_paths:
        print("Git: no markdown to commit.")
        return
    rels = [str(p.relative_to(REPO_ROOT)) for p in md_paths]
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
    days = ", ".join(sorted({p.stem for p in md_paths}))
    msg = f"Sync Garmin daily data ({days})"
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=REPO_ROOT, check=True)
    print("Git: committed and pushed (markdown only; raw JSON stays local).")


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
