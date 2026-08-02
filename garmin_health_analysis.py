"""Offline, non-medical analysis of secure Garmin range exports.

This module never authenticates or calls Garmin. It operates only on a completed
``garmin-health-range`` JSON file so analyses are reproducible from their input.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


ANALYSIS_KINDS = ("data-quality",)
MEDICAL_NOTICE = (
    "Wearable data can support personal trend review but is not a medical diagnosis "
    "or a substitute for professional care."
)

# Keys deliberately cover only stable, documented summary fields. Endpoint-specific
# analysis adds more extractors later rather than guessing a device-specific shape.
METRIC_SPECS = (
    ("resting_heart_rate_bpm", "stats", ("restingHeartRate",)),
    ("steps", "stats", ("totalSteps",)),
    ("hrv_last_night_ms", "hrv", ("hrvSummary", "lastNightAvg")),
    ("hrv_weekly_average_ms", "hrv", ("hrvSummary", "weeklyAvg")),
    ("sleep_duration_seconds", "sleep", ("dailySleepDTO", "sleepTimeSeconds")),
    ("sleep_average_heart_rate_bpm", "sleep", ("dailySleepDTO", "avgHeartRate")),
    ("sleep_average_stress", "sleep", ("dailySleepDTO", "avgSleepStress")),
    ("daily_average_stress", "stress", ("avgStressLevel",)),
    ("training_readiness_score", "training-readiness", ("score",)),
)


class AnalysisInputError(ValueError):
    """Raised when an offline analysis input is not a compatible range export."""


def _date_span(start: str, end: str) -> list[str]:
    current = dt.date.fromisoformat(start)
    final = dt.date.fromisoformat(end)
    return [
        (current + dt.timedelta(days=offset)).isoformat()
        for offset in range((final - current).days + 1)
    ]


def _mapping(value: Any) -> dict[str, Any] | None:
    """Return an endpoint mapping, accepting APIs that return a one-item list."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
    return None


def _is_endpoint_error(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("available") is False
        and isinstance(value.get("error"), dict)
    )


def _number_at(value: Any, path: tuple[str, ...]) -> int | float | None:
    current: Any = _mapping(value)
    for key in path:
        current_mapping = _mapping(current)
        if current_mapping is None or key not in current_mapping:
            return None
        current = current_mapping[key]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return current


def validate_range_export(payload: Any) -> dict[str, Any]:
    """Validate the stable envelope without requiring every optional endpoint."""
    if not isinstance(payload, dict) or payload.get("type") != "garmin-health-range":
        raise AnalysisInputError("analysis input must be a garmin-health-range export")
    start = payload.get("start_date")
    end = payload.get("end_date")
    days = payload.get("days")
    if not isinstance(start, str) or not isinstance(end, str) or not isinstance(days, dict):
        raise AnalysisInputError("range export must contain start_date, end_date, and days")
    try:
        if dt.date.fromisoformat(end) < dt.date.fromisoformat(start):
            raise AnalysisInputError("range export end_date is earlier than start_date")
    except ValueError as exc:
        raise AnalysisInputError("range export dates must use YYYY-MM-DD") from exc
    return payload


def load_range_export(path: Path) -> dict[str, Any]:
    """Read a local export only; callers retain control of where health data lives."""
    try:
        with path.expanduser().open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise AnalysisInputError("analysis input is not valid JSON") from exc
    return validate_range_export(payload)


def _endpoint_names(payload: dict[str, Any], expected_dates: Iterable[str]) -> list[str]:
    names: list[str] = []
    for name in payload.get("kinds") or []:
        if isinstance(name, str) and name not in names:
            names.append(name)
    days = payload["days"]
    for date in expected_dates:
        day = _mapping(days.get(date))
        if not day:
            continue
        for name in day:
            if name not in names:
                names.append(name)
    return names


def metric_value(day: Any, metric_name: str) -> int | float | None:
    """Extract one documented numeric summary value from a daily export payload."""
    spec = next((item for item in METRIC_SPECS if item[0] == metric_name), None)
    if spec is None:
        raise KeyError(f"unknown analysis metric: {metric_name}")
    _, endpoint, path = spec
    day_mapping = _mapping(day)
    if day_mapping is None or _is_endpoint_error(day_mapping.get(endpoint)):
        return None
    return _number_at(day_mapping.get(endpoint), path)


def _endpoint_coverage(
    payload: dict[str, Any], expected_dates: list[str], endpoints: list[str]
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        present_dates: list[str] = []
        available_dates: list[str] = []
        error_dates: list[str] = []
        missing_dates: list[str] = []
        for date in expected_dates:
            day = _mapping(payload["days"].get(date))
            if day is None or endpoint not in day:
                missing_dates.append(date)
                continue
            present_dates.append(date)
            if _is_endpoint_error(day[endpoint]):
                error_dates.append(date)
            elif day[endpoint] is not None:
                available_dates.append(date)
        results[endpoint] = {
            "days_present": len(present_dates),
            "days_available": len(available_dates),
            "days_with_endpoint_error": len(error_dates),
            "availability_rate": round(len(available_dates) / len(expected_dates), 4),
            "missing_dates": missing_dates,
            "endpoint_error_dates": error_dates,
        }
    return results


def analyze_data_quality(payload: Any) -> dict[str, Any]:
    """Report export completeness before interpreting any health metric."""
    export = validate_range_export(payload)
    expected_dates = _date_span(export["start_date"], export["end_date"])
    expected_set = set(expected_dates)
    exported_days = export["days"]
    valid_days = [date for date in expected_dates if _mapping(exported_days.get(date)) is not None]
    missing_dates = [date for date in expected_dates if date not in valid_days]
    unexpected_dates = sorted(
        date for date in exported_days if date not in expected_set
    )
    endpoints = _endpoint_names(export, expected_dates)
    endpoint_coverage = _endpoint_coverage(export, expected_dates, endpoints)
    metric_counts = {
        name: sum(
            metric_value(exported_days.get(date), name) is not None
            for date in expected_dates
        )
        for name, _, _ in METRIC_SPECS
    }
    coverage_rate = len(valid_days) / len(expected_dates)
    if len(valid_days) < 7:
        readiness = "insufficient_for_7_day_descriptive_trends"
    elif coverage_rate < 0.75:
        readiness = "partial_date_coverage"
    elif not any(metric_counts.values()):
        readiness = "no_supported_summary_measurements"
    else:
        readiness = "ready_for_descriptive_trends"

    limitations = [
        "Endpoint availability depends on device, settings, and Garmin synchronization.",
        "Missing or failed endpoints are not interpreted as a physiological value of zero.",
        "This check measures export completeness, not device or medical accuracy.",
    ]
    if missing_dates:
        limitations.append("Some requested dates have no usable exported day payload.")
    if any(item["days_with_endpoint_error"] for item in endpoint_coverage.values()):
        limitations.append("At least one endpoint reported an in-band Garmin or device error.")
    return {
        "schema_version": 1,
        "analysis": "data-quality",
        "source": {
            "type": export["type"],
            "schema_version": export.get("schema_version"),
            "declared_kinds": export.get("kinds") or [],
        },
        "period": {
            "start_date": export["start_date"],
            "end_date": export["end_date"],
            "days_requested": len(expected_dates),
            "days_with_usable_payload": len(valid_days),
            "date_coverage_rate": round(coverage_rate, 4),
            "missing_dates": missing_dates,
            "unexpected_dates": unexpected_dates,
        },
        "endpoints": endpoint_coverage,
        "metric_sample_counts": metric_counts,
        "analysis_readiness": readiness,
        "limitations": limitations,
        "medical_notice": MEDICAL_NOTICE,
    }


def analyze_export(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "data-quality":
        return analyze_data_quality(payload)
    raise AnalysisInputError(f"unsupported analysis kind: {kind}")
