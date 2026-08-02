#!/usr/bin/env python3
"""Read-only Garmin Connect health and activity data CLI.

The CLI intentionally separates concise summaries, raw endpoint payloads, normalized
time series, activity chart streams, and decoded FIT messages. Garmin availability
varies by device and account settings, so unavailable sections are reported in-band.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


DEFAULT_TOKENSTORE = Path(
    os.getenv("GARMIN_TOKENSTORE", "~/.hermes/skills/garmin-health/tokens")
).expanduser()
DEFAULT_MAX_CHART = 100_000
DEFAULT_RANGE_KINDS = ("stats", "hrv", "sleep", "training-readiness")

DAILY_KINDS = (
    "all",
    "stats",
    "heart-rate",
    "hrv",
    "sleep",
    "stress",
    "all-day-stress",
    "body-battery",
    "body-battery-events",
    "steps",
    "spo2",
    "respiration",
    "intensity",
    "training-readiness",
    "morning-readiness",
    "events",
    "floors",
    "rhr",
    "max-metrics",
    "fitness-age",
    "lifestyle",
    "nutrition-food-log",
    "nutrition-meals",
    "nutrition-settings",
)

SERIES_KINDS = (
    "heart-rate",
    "hrv",
    "stress",
    "body-battery",
    "respiration",
    "spo2",
    "steps",
    "sleep-levels",
    "sleep-movement",
    "sleep-heart-rate",
    "sleep-hrv",
    "sleep-stress",
    "sleep-body-battery",
    "sleep-spo2",
    "sleep-respiration",
    "breathing-disruption",
)

LARGE_RAW_KINDS = {"all", "sleep"}


def series_semantics(kind: str) -> dict[str, str]:
    """Describe what the normalized series means without inventing precision."""
    shared = {
        "source": "Garmin Connect data synchronized from the user's device",
        "missing_values": "Raw null and Garmin sentinel values are preserved.",
        "medical_notice": "Garmin data is not intended to diagnose, treat, cure, or prevent disease.",
    }
    if kind == "heart-rate":
        return {
            **shared,
            "metric": "All-day heart rate",
            "unit": "bpm",
            "granularity": "Garmin Connect generally displays all-day heart rate as two-minute averages; gaps are not interpolated.",
        }
    if kind in {"hrv", "sleep-hrv"}:
        return {
            **shared,
            "metric": "Garmin HRV Status sleep reading",
            "unit": "milliseconds as provided by Garmin",
            "granularity": "Use Garmin-provided timestamps; this is not a timestamped beat-to-beat RR series.",
        }
    if kind.startswith("sleep-") or kind in {"sleep-levels", "sleep-movement", "breathing-disruption"}:
        return {
            **shared,
            "metric": "Sleep-period data",
            "granularity": "Use the source timestamp field and inspect each record's Garmin-provided duration where present.",
        }
    return {
        **shared,
        "metric": kind,
        "granularity": "Use the raw descriptor and timestamp fields supplied with each Garmin response.",
    }


def json_default(value: Any) -> str:
    """Serialize SDK values without dropping information from raw payloads."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def resolve_date(value: str | None) -> str:
    if value is None:
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("日期必须为 YYYY-MM-DD") from exc


def resolve_range(start: str, end: str | None) -> tuple[str, str]:
    start_date = resolve_date(start)
    end_date = resolve_date(end) if end else start_date
    if end_date < start_date:
        raise ValueError("结束日期不能早于开始日期")
    return start_date, end_date


def date_span(start: str, end: str):
    """Yield inclusive ISO dates without silently changing the requested range."""
    current = dt.date.fromisoformat(start)
    final = dt.date.fromisoformat(end)
    while current <= final:
        yield current.isoformat()
        current += dt.timedelta(days=1)


def positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return result


def non_negative_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是非负数") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("必须是非负数")
    return result


def parse_timezone(value: str) -> dt.tzinfo:
    if value.upper() == "UTC":
        return dt.timezone.utc
    try:
        sign = 1 if value.startswith("+") else -1 if value.startswith("-") else 0
        hours, minutes = value[1:].split(":", 1)
        if sign == 0:
            raise ValueError
        offset = sign * (int(hours) * 60 + int(minutes))
        if abs(offset) > 14 * 60:
            raise ValueError
        return dt.timezone(dt.timedelta(minutes=offset))
    except (ValueError, IndexError) as exc:
        raise ValueError("时区使用 UTC 或 +08:00 形式") from exc


def format_time(
    value: Any, tz: dt.tzinfo, *, source_is_utc: bool = False
) -> str | None:
    """Format epoch milliseconds or ISO timestamps without guessing missing time."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Garmin chart endpoints use Unix epoch milliseconds.
        return (
            dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc)
            .astimezone(tz)
            .isoformat()
        )
    if isinstance(value, dt.datetime):
        return value.astimezone(tz).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo:
                return parsed.astimezone(tz).isoformat()
            if source_is_utc:
                return parsed.replace(tzinfo=dt.timezone.utc).astimezone(tz).isoformat()
        except ValueError:
            pass
    return str(value)


def get_client(tokenstore: Path):
    from garminconnect import Garmin

    client = Garmin(
        os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"), is_cn=True
    )
    client.login(str(tokenstore))
    return client


def safe_call(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as exc:  # Device support and server response vary by endpoint.
        return {
            "available": False,
            "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
        }


def is_error(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("available") is False
        and isinstance(value.get("error"), dict)
    )


def daily_getters(client: Any, date: str) -> dict[str, Callable[[], Any]]:
    return {
        "stats": lambda: client.get_stats(date),
        "heart-rate": lambda: client.get_heart_rates(date),
        "hrv": lambda: client.get_hrv_data(date),
        "sleep": lambda: client.get_sleep_data(date),
        "stress": lambda: client.get_stress_data(date),
        "all-day-stress": lambda: client.get_all_day_stress(date),
        "body-battery": lambda: client.get_body_battery(date, date),
        "body-battery-events": lambda: client.get_body_battery_events(date),
        "steps": lambda: client.get_steps_data(date),
        "spo2": lambda: client.get_spo2_data(date),
        "respiration": lambda: client.get_respiration_data(date),
        "intensity": lambda: client.get_intensity_minutes_data(date),
        "training-readiness": lambda: client.get_training_readiness(date),
        "morning-readiness": lambda: client.get_morning_training_readiness(date),
        "events": lambda: client.get_all_day_events(date),
        "floors": lambda: client.get_floors(date),
        "rhr": lambda: client.get_rhr_day(date),
        "max-metrics": lambda: client.get_max_metrics(date),
        "fitness-age": lambda: client.get_fitnessage_data(date),
        "lifestyle": lambda: client.get_lifestyle_logging_data(date),
        "nutrition-food-log": lambda: client.get_nutrition_daily_food_log(date),
        "nutrition-meals": lambda: client.get_nutrition_daily_meals(date),
        "nutrition-settings": lambda: client.get_nutrition_daily_settings(date),
    }


def write_secure_json(path: Path, payload: Any, *, force: bool) -> None:
    """Atomically write sensitive exports with owner-only permissions."""
    path = path.expanduser()
    if path.exists() and not force:
        raise FileExistsError(f"目标已存在：{path}；如确认覆盖请加 --force")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=json_default)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def save_or_print(
    payload: Any, args: argparse.Namespace, *, requires_output: bool = False
) -> None:
    output = getattr(args, "output", None)
    stdout = getattr(args, "stdout", False)
    if output and stdout:
        raise ValueError("--output 与 --stdout 不能同时使用")
    if output:
        path = Path(output)
        write_secure_json(path, payload, force=getattr(args, "force", False))
        print(
            json.dumps(
                {"saved": str(path.expanduser()), "bytes": path.expanduser().stat().st_size},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if requires_output and not stdout:
        raise ValueError("此命令可能包含大量敏感数据；请使用 --output PATH，或明确加 --stdout")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))


def descriptor_map(descriptors: Any) -> dict[int, str]:
    """Normalize descriptor variants returned by Garmin chart endpoints."""
    output: dict[int, str] = {}
    for item in descriptors or []:
        if not isinstance(item, dict):
            continue
        index = next(
            (
                value
                for key, value in item.items()
                if key.lower().endswith("index") and isinstance(value, int)
            ),
            None,
        )
        key = next(
            (
                value
                for key, value in item.items()
                if key.lower().endswith("key") and isinstance(value, str)
            ),
            None,
        )
        if index is not None:
            output[index] = key or f"column_{index}"
    return output


def chart_series(
    source: dict[str, Any], values_key: str, descriptors_key: str, tz: dt.tzinfo
) -> dict[str, Any]:
    descriptors = descriptor_map(source.get(descriptors_key))
    timestamp_index = next(
        (index for index, key in descriptors.items() if "timestamp" in key.lower()), 0
    )
    records = []
    for row in source.get(values_key) or []:
        if not isinstance(row, list):
            continue
        timestamp = row[timestamp_index] if timestamp_index < len(row) else None
        fields = {
            descriptors.get(index, f"column_{index}"): value
            for index, value in enumerate(row)
            if index != timestamp_index
        }
        records.append(
            {
                "timestamp": timestamp,
                "time": format_time(timestamp, tz),
                "values": fields,
            }
        )
    return {
        "record_count": len(records),
        "descriptors": source.get(descriptors_key),
        "records": records,
        "raw_value_key": values_key,
    }


def object_series(
    rows: Any, time_keys: tuple[str, ...], tz: dt.tzinfo
) -> dict[str, Any]:
    records = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        time_key = next((key for key in time_keys if row.get(key) is not None), None)
        timestamp = row.get(time_key) if time_key else None
        records.append(
            {
                "timestamp": timestamp,
                "time_field": time_key,
                "time": format_time(
                    timestamp, tz, source_is_utc="gmt" in (time_key or "").lower()
                ),
                "values": {key: value for key, value in row.items() if key != time_key},
            }
        )
    return {"record_count": len(records), "records": records}


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    try:
        client = get_client(args.tokenstore)
        devices = safe_call(client.get_devices)
        return {
            "authenticated": True,
            "unit_system": safe_call(client.get_unit_system),
            "device_count": len(devices) if isinstance(devices, list) else None,
            "tokenstore": str(args.tokenstore),
        }
    except Exception as exc:
        return {
            "authenticated": False,
            "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
        }


def cmd_login(args: argparse.Namespace) -> dict[str, Any]:
    from garminconnect import Garmin

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise ValueError("请通过 GARMIN_EMAIL 和 GARMIN_PASSWORD 环境变量提供登录凭据")
    client = Garmin(email, password, is_cn=True)
    state, _ = client.login()
    if state == "needs_mfa":
        return {"authenticated": False, "requires_mfa": True}
    args.tokenstore.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(args.tokenstore, 0o700)
    client.client.dump(str(args.tokenstore))
    for path in args.tokenstore.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)
    return {"authenticated": True, "tokenstore": str(args.tokenstore)}


def cmd_raw(args: argparse.Namespace) -> dict[str, Any]:
    date = resolve_date(args.date)
    client = get_client(args.tokenstore)
    getters = daily_getters(client, date)
    kinds = tuple(getters) if args.kind == "all" else (args.kind,)
    return {"date": date, "data": {kind: safe_call(getters[kind]) for kind in kinds}}


def cmd_overview(args: argparse.Namespace) -> dict[str, Any]:
    date = resolve_date(args.date)
    client = get_client(args.tokenstore)
    needed = ("stats", "sleep", "hrv", "spo2", "respiration", "stress", "training-readiness")
    getters = daily_getters(client, date)
    raw = {kind: safe_call(getters[kind]) for kind in needed}

    stats = raw["stats"] if isinstance(raw["stats"], dict) and not is_error(raw["stats"]) else {}
    sleep_data = raw["sleep"] if isinstance(raw["sleep"], dict) and not is_error(raw["sleep"]) else {}
    sleep = sleep_data.get("dailySleepDTO") or {}
    hrv_data = raw["hrv"] if isinstance(raw["hrv"], dict) and not is_error(raw["hrv"]) else {}
    hrv = hrv_data.get("hrvSummary") or {}
    spo2 = raw["spo2"] if isinstance(raw["spo2"], dict) and not is_error(raw["spo2"]) else {}
    respiration = raw["respiration"] if isinstance(raw["respiration"], dict) and not is_error(raw["respiration"]) else {}
    stress = raw["stress"] if isinstance(raw["stress"], dict) and not is_error(raw["stress"]) else {}
    readiness = raw["training-readiness"]
    if isinstance(readiness, list):
        readiness = readiness[0] if readiness else {}

    def pick(data: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
        return {name: data.get(name) for name in names if name in data}

    return {
        "date": date,
        "summary": pick(
            stats,
            (
                "totalSteps",
                "totalDistanceMeters",
                "activeKilocalories",
                "totalKilocalories",
                "restingHeartRate",
                "minHeartRate",
                "maxHeartRate",
                "averageStressLevel",
                "maxStressLevel",
                "sleepingSeconds",
                "bodyBatteryMostRecentValue",
                "bodyBatteryHighestValue",
                "bodyBatteryLowestValue",
                "averageSpo2",
                "lowestSpo2",
            ),
        ),
        "sleep": pick(
            sleep,
            (
                "sleepTimeSeconds",
                "deepSleepSeconds",
                "lightSleepSeconds",
                "remSleepSeconds",
                "awakeSleepSeconds",
                "averageSpO2Value",
                "lowestSpO2Value",
                "averageRespirationValue",
                "avgHeartRate",
                "avgSleepStress",
                "sleepScores",
            ),
        ),
        "hrv": {
            **pick(hrv, ("weeklyAvg", "lastNightAvg", "lastNight5MinHigh", "baseline", "status", "feedbackPhrase")),
            "reading_count": len(hrv_data.get("hrvReadings") or []),
            "sleep_embedded_reading_count": len(sleep_data.get("hrvData") or []),
        },
        "oxygen_and_respiration": {
            **pick(spo2, ("averageSpO2", "lowestSpO2", "latestSpO2", "avgSleepSpO2")),
            **pick(respiration, ("lowestRespirationValue", "highestRespirationValue", "avgWakingRespirationValue", "avgSleepRespirationValue")),
        },
        "stress": pick(stress, ("avgStressLevel", "maxStressLevel")),
        "training_readiness": pick(
            readiness if isinstance(readiness, dict) else {},
            ("score", "level", "sleepScore", "recoveryTime", "acuteLoad", "hrvWeeklyAverage"),
        ),
        "availability": {
            kind: "unavailable" if is_error(value) else "available"
            for kind, value in raw.items()
        },
    }


def cmd_series(args: argparse.Namespace) -> dict[str, Any]:
    date = resolve_date(args.date)
    kind = args.kind
    result: dict[str, Any] = {
        "date": date,
        "series": kind,
        "timezone": args.timezone,
        "semantics": series_semantics(kind),
    }
    try:
        tz = parse_timezone(args.timezone)
        client = get_client(args.tokenstore)
        daily = daily_getters(client, date)
        if kind in {"heart-rate", "stress", "respiration"}:
            source = daily[kind]() or {}
            names = {
                "heart-rate": ("heartRateValues", "heartRateValueDescriptors"),
                "stress": ("stressValuesArray", "stressValueDescriptorsDTOList"),
                "respiration": ("respirationValuesArray", "respirationValueDescriptorsDTOList"),
            }
            values_key, descriptors_key = names[kind]
            data = chart_series(source, values_key, descriptors_key, tz)
        elif kind == "body-battery":
            source = daily[kind]() or []
            day = source[0] if isinstance(source, list) and source else {}
            data = chart_series(day, "bodyBatteryValuesArray", "bodyBatteryValueDescriptorDTOList", tz)
        elif kind == "spo2":
            source = daily[kind]() or {}
            rows = (
                source.get("spO2SingleValues")
                or source.get("continuousReadingDTOList")
                or source.get("spO2HourlyAverages")
                or []
            )
            data = object_series(
                rows, ("epochTimestamp", "startTimeGMT", "timestampGMT", "timestampLocal"), tz
            )
            data["daily_summary"] = {
                key: source.get(key)
                for key in ("averageSpO2", "lowestSpO2", "latestSpO2", "avgSleepSpO2")
            }
        elif kind == "steps":
            data = object_series(
                daily[kind]() or [], ("startGMT", "startTimeGMT", "startTimestampGMT"), tz
            )
        elif kind == "hrv":
            source = daily[kind]() or {}
            data = object_series(
                source.get("hrvReadings"), ("readingTimeGMT", "readingTimeLocal"), tz
            )
            data["summary"] = source.get("hrvSummary")
        else:
            source = daily["sleep"]() or {}
            mapping = {
                "sleep-levels": ("sleepLevels", ("startGMT",)),
                "sleep-movement": ("sleepMovement", ("startGMT",)),
                "sleep-heart-rate": ("sleepHeartRate", ("startGMT",)),
                "sleep-hrv": ("hrvData", ("startGMT",)),
                "sleep-stress": ("sleepStress", ("startGMT",)),
                "sleep-body-battery": ("sleepBodyBattery", ("startGMT",)),
                "sleep-spo2": ("wellnessEpochSPO2DataDTOList", ("epochTimestamp",)),
                "sleep-respiration": ("wellnessEpochRespirationDataDTOList", ("startTimeGMT",)),
                "breathing-disruption": ("breathingDisruptionData", ("startGMT",)),
            }
            rows_key, time_keys = mapping[kind]
            data = object_series(source.get(rows_key), time_keys, tz)
    except Exception as exc:
        return {
            **result,
            "available": False,
            "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
        }
    return {**result, "available": True, "data": data}


def cmd_export_day(args: argparse.Namespace) -> dict[str, Any]:
    date = resolve_date(args.date)
    client = get_client(args.tokenstore)
    getters = daily_getters(client, date)
    return {"date": date, "data": {kind: safe_call(call) for kind, call in getters.items()}}


def cmd_export_range(args: argparse.Namespace) -> dict[str, Any]:
    """Export daily endpoints over a date range with an atomic per-day checkpoint."""
    if args.stdout:
        raise ValueError("export-range 只写入文件；请移除 --stdout 并提供 --output PATH")
    if not args.output:
        raise ValueError("export-range 包含大量敏感数据；必须提供 --output PATH")
    if args.resume and args.force:
        raise ValueError("--resume 不能与 --force 同时使用")
    if args.all_kinds and args.kind:
        raise ValueError("--all 不能与 --kind 同时使用")
    if args.resume and (args.all_kinds or args.kind):
        raise ValueError("--resume 使用已有导出的端点列表；请不要同时提供 --kind 或 --all")

    start, end = resolve_range(args.start_date, args.end_date)
    output_path = Path(args.output).expanduser()
    days = list(date_span(start, end))
    if args.resume:
        if not output_path.is_file():
            raise FileNotFoundError(f"找不到要续传的导出文件：{output_path}")
        with output_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("type") != "garmin-health-range":
            raise ValueError("--resume 的文件不是 garmin-health-range 导出")
        if payload.get("start_date") != start or payload.get("end_date") != end:
            raise ValueError("--resume 的日期范围必须与本次请求完全一致")
        kinds = tuple(payload.get("kinds") or ())
        if not kinds or any(kind not in DAILY_KINDS[1:] for kind in kinds):
            raise ValueError("--resume 的文件缺少有效的日级端点列表")
        if not isinstance(payload.get("days"), dict):
            raise ValueError("--resume 的文件没有有效的 days 对象")
    else:
        if output_path.exists() and not args.force:
            raise FileExistsError(f"目标已存在：{output_path}；如确认覆盖请加 --force")
        kinds = (
            DAILY_KINDS[1:]
            if args.all_kinds
            else tuple(args.kind or DEFAULT_RANGE_KINDS)
        )
        payload = {
            "schema_version": 1,
            "type": "garmin-health-range",
            "start_date": start,
            "end_date": end,
            "kinds": list(kinds),
            "days": {},
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    fetched = 0
    pending_dates = [date for date in days if date not in payload["days"]]
    skipped = len(days) - len(pending_dates)
    if pending_dates:
        client = get_client(args.tokenstore)
    for index, date in enumerate(pending_dates):
        getters = daily_getters(client, date)
        payload["days"][date] = {
            kind: safe_call(getters[kind]) for kind in kinds
        }
        payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        # A completed day is immediately recoverable after interruption or rate limiting.
        write_secure_json(output_path, payload, force=True)
        fetched += 1
        if args.delay and index < len(pending_dates) - 1:
            time.sleep(args.delay)

    if not output_path.exists():
        # Covers an empty/reused range that did not need a new checkpoint.
        write_secure_json(output_path, payload, force=args.force)
    args._output_already_written = True
    return {
        "saved": str(output_path),
        "start_date": start,
        "end_date": end,
        "kinds": list(kinds),
        "days_total": len(days),
        "days_fetched": fetched,
        "days_skipped": skipped,
        "bytes": output_path.stat().st_size,
    }


def cmd_activities(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    activities = client.get_activities(args.start, args.limit, args.type)
    if isinstance(activities, dict):
        activities = activities.get("activities") or activities.get("activityList") or activities
    return {"start": args.start, "limit": args.limit, "activity_type": args.type, "data": activities}


def cmd_activity(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    activity_id = str(args.activity_id)
    calls: dict[str, Callable[[], Any]] = {
        "summary": lambda: client.get_activity(activity_id),
        "splits": lambda: client.get_activity_splits(activity_id),
        "typed_splits": lambda: client.get_activity_typed_splits(activity_id),
        "split_summaries": lambda: client.get_activity_split_summaries(activity_id),
        "exercise_sets": lambda: client.get_activity_exercise_sets(activity_id),
        "weather": lambda: client.get_activity_weather(activity_id),
        "heart_rate_timezones": lambda: client.get_activity_hr_in_timezones(activity_id),
        "power_timezones": lambda: client.get_activity_power_in_timezones(activity_id),
        "gear": lambda: client.get_activity_gear(activity_id),
    }
    return {"activity_id": activity_id, "data": {name: safe_call(call) for name, call in calls.items()}}


def cmd_activity_stream(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    activity_id = str(args.activity_id)
    source = client.get_activity_details(
        activity_id, maxchart=args.max_chart, maxpoly=args.max_poly
    )
    descriptors = source.get("metricDescriptors") or []
    index = descriptor_map(descriptors)
    timestamp_index = next(
        (position for position, key in index.items() if "timestamp" in key.lower()), None
    )
    records = []
    for item in source.get("activityDetailMetrics") or []:
        values = item.get("metrics") if isinstance(item, dict) else None
        if not isinstance(values, list):
            continue
        timestamp = values[timestamp_index] if timestamp_index is not None and timestamp_index < len(values) else None
        records.append(
            {
                "timestamp": timestamp,
                "time": format_time(timestamp, parse_timezone(args.timezone)),
                "values": {
                    index.get(position, f"column_{position}"): value
                    for position, value in enumerate(values)
                    if position != timestamp_index
                },
            }
        )
    total = source.get("totalMetricsCount")
    returned = source.get("metricsCount", len(records))
    output: dict[str, Any] = {
        "activity_id": activity_id,
        "record_count": len(records),
        "metrics_count_returned": returned,
        "total_metrics_count": total,
        "truncated": isinstance(total, int) and isinstance(returned, int) and returned < total,
        "metric_descriptors": descriptors,
        "records": records,
    }
    if args.include_route:
        output["geo_polyline"] = source.get("geoPolylineDTO")
    return output


def activity_fit_bytes(client: Any, activity_id: str) -> tuple[str, bytes]:
    raw = client.download_activity(
        activity_id, client.ActivityDownloadFormat.ORIGINAL
    )
    if raw[:2] != b"PK":
        return f"{activity_id}.fit", raw
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entry = next(
            (item for item in archive.infolist() if item.filename.lower().endswith(".fit")),
            None,
        )
        if entry is None:
            raise ValueError("原始活动 ZIP 中未找到 .fit 文件")
        return entry.filename, archive.read(entry)


def fit_unknown_field_metadata(field: Any) -> dict[str, Any]:
    """Retain the FIT definition information when a profile field is unknown."""
    base_type = getattr(field, "base_type", None)
    field_definition = getattr(field, "field_def", None)
    return {
        "name": field.name,
        "field_number": getattr(field, "def_num", None),
        "base_type": getattr(base_type, "name", str(base_type) if base_type else None),
        "is_developer_field": bool(getattr(field_definition, "is_dev", False)),
        "units": getattr(field, "units", None),
        "value": field.value,
        "raw_value": getattr(field, "raw_value", None),
    }


def fit_messages(client: Any, activity_id: str):
    try:
        import fitdecode
    except ImportError as exc:
        raise RuntimeError("缺少 fitdecode；请运行 requirements.txt 安装") from exc

    filename, contents = activity_fit_bytes(client, activity_id)
    with fitdecode.FitReader(io.BytesIO(contents)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            unknown_fields = []
            fields = {
                field.name: field.value
                for field in frame.fields
                if field.value is not None
            }
            for field in frame.fields:
                if field.value is not None and field.name.startswith("unknown"):
                    unknown_fields.append(fit_unknown_field_metadata(field))
            metadata = {
                "global_message_number": getattr(frame.def_mesg, "global_mesg_num", None),
                "is_developer_message": frame.is_developer_data,
                "unknown_fields": unknown_fields,
            }
            yield filename, frame.name, fields, metadata


def cmd_fit_summary(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    counts: Counter[str] = Counter()
    fields: dict[str, set[str]] = defaultdict(set)
    unknown_definitions: dict[str, set[str]] = defaultdict(set)
    filename = None
    for filename, message, values, metadata in fit_messages(client, str(args.activity_id)):
        counts[message] += 1
        fields[message].update(values)
        for field in metadata["unknown_fields"]:
            unknown_definitions[message].add(
                "field {field_number} ({base_type}{developer})".format(
                    field_number=field["field_number"],
                    base_type=field["base_type"] or "unknown base type",
                    developer=", developer" if field["is_developer_field"] else "",
                )
            )
    return {
        "activity_id": str(args.activity_id),
        "fit_file": filename,
        "message_counts": dict(sorted(counts.items())),
        "fields_by_message": {name: sorted(names) for name, names in sorted(fields.items())},
        "unrecognized_field_definitions": {
            name: sorted(definitions)
            for name, definitions in sorted(unknown_definitions.items())
        },
        "hrv_note": "hrv 消息包含 RR 间期，但本身无时间戳；用相邻 record/event 和 RR 顺序对齐。",
    }


def cmd_fit_stream(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    activity_id = str(args.activity_id)
    records = []
    filename = None
    for filename, message, fields, metadata in fit_messages(client, activity_id):
        if args.message != "all" and message != args.message:
            continue
        record: dict[str, Any] = {"message": message, "fields": fields}
        if message.startswith("unknown") or metadata["unknown_fields"]:
            record["message_definition"] = {
                "global_message_number": metadata["global_message_number"],
                "is_developer_message": metadata["is_developer_message"],
            }
        if metadata["unknown_fields"]:
            # Values are also retained in fields; this adds stable field numbers,
            # base types and raw values for a future profile-specific decoder.
            record["unrecognized_fields"] = metadata["unknown_fields"]
        if message == "hrv":
            record["timestamp_semantics"] = "RR 间期没有自身时间戳；保持消息顺序并与相邻 record/event 对齐。"
        records.append(record)
    return {
        "activity_id": activity_id,
        "fit_file": filename,
        "message_filter": args.message,
        "record_count": len(records),
        "records": records,
    }


def cmd_performance(args: argparse.Namespace) -> dict[str, Any]:
    date = resolve_date(args.date)
    client = get_client(args.tokenstore)
    start = (dt.date.fromisoformat(date) - dt.timedelta(days=42)).isoformat()
    calls: dict[str, Callable[[], Any]] = {
        "training_status": lambda: client.get_training_status(date),
        "max_metrics": lambda: client.get_max_metrics(date),
        "training_readiness": lambda: client.get_training_readiness(date),
        "morning_training_readiness": lambda: client.get_morning_training_readiness(date),
        "endurance_score": lambda: client.get_endurance_score(start, date),
        "hill_score": lambda: client.get_hill_score(start, date),
        "lactate_threshold": lambda: client.get_lactate_threshold(),
        # The wrapper requires all three filtering parameters or none. The unfiltered
        # endpoint returns the currently available prediction set without fabricating
        # a sport type.
        "race_predictions": client.get_race_predictions,
        "running_tolerance": lambda: client.get_running_tolerance(start, date),
    }
    return {"date": date, "lookback_start": start, "data": {name: safe_call(call) for name, call in calls.items()}}


def cmd_profile(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client(args.tokenstore)
    calls: dict[str, Callable[[], Any]] = {
        "user_profile": client.get_user_profile,
        "user_settings": client.get_userprofile_settings,
        "devices": client.get_devices,
        "last_used_device": client.get_device_last_used,
        "primary_training_device": client.get_primary_training_device,
        "heart_rate_zones": client.get_heart_rate_zones,
        "power_zones": client.get_power_zones,
        "cycling_ftp": client.get_cycling_ftp,
        "personal_records": client.get_personal_record,
    }
    return {"data": {name: safe_call(call) for name, call in calls.items()}}


def cmd_body(args: argparse.Namespace) -> dict[str, Any]:
    start, end = resolve_range(args.start_date, args.end_date)
    if args.kind == "hydration" and start != end:
        raise ValueError("hydration endpoint supports one date only; provide start_date without end_date")
    client = get_client(args.tokenstore)
    calls: dict[str, Callable[[], Any]] = {
        "body-composition": lambda: client.get_body_composition(start, end),
        "weigh-ins": lambda: client.get_weigh_ins(start, end),
        "blood-pressure": lambda: client.get_blood_pressure(start, end),
        "hydration": lambda: client.get_hydration_data(start),
    }
    return {"start_date": start, "end_date": end, "kind": args.kind, "data": safe_call(calls[args.kind])}


def cmd_reproductive(args: argparse.Namespace) -> dict[str, Any]:
    """Read reproductive-health data only when the user explicitly requests it."""
    client = get_client(args.tokenstore)
    if args.kind == "menstrual-calendar":
        if not args.start_date:
            raise ValueError("menstrual-calendar requires start_date")
        start, end = resolve_range(args.start_date, args.end_date)
        data = safe_call(lambda: client.get_menstrual_calendar_data(start, end))
        return {
            "kind": args.kind,
            "start_date": start,
            "end_date": end,
            "data": data,
        }
    if args.start_date or args.end_date:
        raise ValueError("pregnancy-summary does not accept dates")
    return {"kind": args.kind, "data": safe_call(client.get_pregnancy_summary)}


def add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Explicitly permit printing raw or sensitive data to standard output",
    )
    parser.add_argument("--output", help="将完整 JSON 写入指定文件")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的 --output 文件")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Garmin Connect 全量健康与活动数据（中国区）")
    parser.add_argument(
        "--tokenstore",
        type=Path,
        default=DEFAULT_TOKENSTORE,
        help="token 目录；默认复用旧 garmin skill 的 token",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="验证 token 和设备连接")
    status.set_defaults(handler=cmd_status)

    login = sub.add_parser("login", help="从环境变量重新登录并保存 token")
    login.set_defaults(handler=cmd_login)

    overview = sub.add_parser("overview", help="日常健康概览和数据可用性")
    overview.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    add_output_options(overview)
    overview.set_defaults(handler=cmd_overview)

    raw = sub.add_parser("raw", help="读取 Garmin 日级原始端点")
    raw.add_argument("kind", choices=DAILY_KINDS)
    raw.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    add_output_options(raw)
    raw.set_defaults(handler=cmd_raw)

    series = sub.add_parser("series", help="读取标准化的时序数据")
    series.add_argument("kind", choices=SERIES_KINDS)
    series.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    series.add_argument("--timezone", default="+08:00", help="显示时区，默认 +08:00")
    add_output_options(series)
    series.set_defaults(handler=cmd_series)

    export_day = sub.add_parser("export-day", help="导出全部日级端点")
    export_day.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    add_output_options(export_day)
    export_day.set_defaults(handler=cmd_export_day)

    export_range = sub.add_parser(
        "export-range", help="按日导出历史数据，并在每个日期完成后保存可续传检查点"
    )
    export_range.add_argument("start_date", help="YYYY-MM-DD")
    export_range.add_argument("end_date", help="YYYY-MM-DD")
    export_range.add_argument(
        "--kind",
        action="append",
        choices=DAILY_KINDS[1:],
        help="要导出的日级端点；可重复。缺省为核心健康端点。",
    )
    export_range.add_argument("--all", dest="all_kinds", action="store_true", help="导出所有日级端点")
    export_range.add_argument("--resume", action="store_true", help="从同范围的已有导出文件续传")
    export_range.add_argument(
        "--delay", type=non_negative_float, default=0.25, help="每个日期之间等待的秒数（默认 0.25）"
    )
    add_output_options(export_range)
    export_range.set_defaults(handler=cmd_export_range)

    activities = sub.add_parser("activities", help="列出原始活动记录")
    activities.add_argument("--limit", type=positive_int, default=20)
    activities.add_argument("--start", type=int, default=0)
    activities.add_argument("--type", help="活动类型，如 running、cycling")
    add_output_options(activities)
    activities.set_defaults(handler=cmd_activities)

    activity = sub.add_parser("activity", help="读取单次活动的摘要、分段等详情")
    activity.add_argument("activity_id", type=int)
    add_output_options(activity)
    activity.set_defaults(handler=cmd_activity)

    stream = sub.add_parser("activity-stream", help="读取单次活动的完整图表时序")
    stream.add_argument("activity_id", type=int)
    stream.add_argument("--max-chart", type=positive_int, default=DEFAULT_MAX_CHART)
    stream.add_argument("--max-poly", type=positive_int, default=DEFAULT_MAX_CHART)
    stream.add_argument("--timezone", default="+08:00")
    stream.add_argument("--include-route", action="store_true", help="附带压缩路线 polyline")
    add_output_options(stream)
    stream.set_defaults(handler=cmd_activity_stream)

    fit_summary = sub.add_parser("fit-summary", help="统计活动原始 FIT 中可用的消息和字段")
    fit_summary.add_argument("activity_id", type=int)
    add_output_options(fit_summary)
    fit_summary.set_defaults(handler=cmd_fit_summary)

    fit_stream = sub.add_parser("fit-stream", help="解码活动原始 FIT 的动态消息")
    fit_stream.add_argument("activity_id", type=int)
    fit_stream.add_argument("--message", default="record", help="record、hrv 或 all")
    add_output_options(fit_stream)
    fit_stream.set_defaults(handler=cmd_fit_stream)

    performance = sub.add_parser("performance", help="读取训练与表现相关指标")
    performance.add_argument("date", nargs="?", help="YYYY-MM-DD，默认今天")
    add_output_options(performance)
    performance.set_defaults(handler=cmd_performance)

    profile = sub.add_parser("profile", help="读取设备、分区、FTP 和个人纪录")
    add_output_options(profile)
    profile.set_defaults(handler=cmd_profile)

    body = sub.add_parser("body", help="读取体成分、体重、血压或饮水数据")
    body.add_argument("kind", choices=("body-composition", "weigh-ins", "blood-pressure", "hydration"))
    body.add_argument("start_date", help="YYYY-MM-DD")
    body.add_argument("end_date", nargs="?", help="YYYY-MM-DD；缺省同起始日期")
    add_output_options(body)
    body.set_defaults(handler=cmd_body)

    reproductive = sub.add_parser(
        "reproductive", help="读取经期日历或妊娠摘要（仅在用户明确请求时使用）"
    )
    reproductive.add_argument("kind", choices=("menstrual-calendar", "pregnancy-summary"))
    reproductive.add_argument("start_date", nargs="?", help="经期日历的 YYYY-MM-DD")
    reproductive.add_argument("end_date", nargs="?", help="经期日历的 YYYY-MM-DD")
    add_output_options(reproductive)
    reproductive.set_defaults(handler=cmd_reproductive)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        payload = args.handler(args)
        if getattr(args, "_output_already_written", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))
            return
        # Health, activity, profile and reproductive payloads can be extensive and
        # sensitive. A caller must opt into terminal disclosure with --stdout.
        requires_output = args.command not in {"status", "login"}
        save_or_print(payload, args, requires_output=requires_output)
    except Exception as exc:
        print(
            json.dumps(
                {"error": {"type": type(exc).__name__, "message": str(exc)[:500]}},
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
