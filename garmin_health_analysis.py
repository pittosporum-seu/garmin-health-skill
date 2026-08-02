"""Offline, non-medical analysis of secure Garmin range exports.

This module never authenticates or calls Garmin. It operates only on a completed
``garmin-health-range`` JSON file so analyses are reproducible from their input.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


ANALYSIS_KINDS = ("data-quality", "recovery", "sleep", "stress-energy")
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
    ("sleep_deep_seconds", "sleep", ("dailySleepDTO", "deepSleepSeconds")),
    ("sleep_light_seconds", "sleep", ("dailySleepDTO", "lightSleepSeconds")),
    ("sleep_rem_seconds", "sleep", ("dailySleepDTO", "remSleepSeconds")),
    ("sleep_awake_seconds", "sleep", ("dailySleepDTO", "awakeSleepSeconds")),
    ("sleep_average_heart_rate_bpm", "sleep", ("dailySleepDTO", "avgHeartRate")),
    ("sleep_average_stress", "sleep", ("dailySleepDTO", "avgSleepStress")),
    ("daily_average_stress", "stress", ("avgStressLevel",)),
    ("training_readiness_score", "training-readiness", ("score",)),
)
RECOVERY_METRICS = (
    "hrv_last_night_ms",
    "resting_heart_rate_bpm",
    "sleep_duration_seconds",
    "training_readiness_score",
)
MIN_BASELINE_SAMPLES = 7
MAX_BASELINE_SAMPLES = 28
SLEEP_METRICS = (
    "sleep_duration_seconds",
    "sleep_deep_seconds",
    "sleep_light_seconds",
    "sleep_rem_seconds",
    "sleep_awake_seconds",
    "sleep_average_heart_rate_bpm",
    "sleep_average_stress",
    "hrv_last_night_ms",
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


def _value_at(value: Any, path: tuple[str, ...]) -> Any:
    current: Any = _mapping(value)
    for key in path:
        current_mapping = _mapping(current)
        if current_mapping is None or key not in current_mapping:
            return None
        current = current_mapping[key]
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


def _median(values: list[int | float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _rounded(value: int | float) -> int | float:
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded


def _metric_label(metric_name: str) -> str:
    labels = {
        "hrv_last_night_ms": "Garmin sleep HRV Status reading",
        "resting_heart_rate_bpm": "resting heart rate",
        "sleep_duration_seconds": "sleep duration",
        "sleep_deep_seconds": "deep sleep duration",
        "sleep_light_seconds": "light sleep duration",
        "sleep_rem_seconds": "REM sleep duration",
        "sleep_awake_seconds": "awake duration during the sleep period",
        "sleep_average_heart_rate_bpm": "average sleep heart rate",
        "sleep_average_stress": "average sleep stress",
        "daily_average_stress": "average daily Garmin stress",
        "steps": "steps",
        "training_readiness_score": "Garmin training readiness score",
    }
    return labels[metric_name]


def _metric_unit(metric_name: str) -> str:
    units = {
        "hrv_last_night_ms": "milliseconds as provided by Garmin",
        "resting_heart_rate_bpm": "bpm",
        "sleep_duration_seconds": "seconds",
        "sleep_deep_seconds": "seconds",
        "sleep_light_seconds": "seconds",
        "sleep_rem_seconds": "seconds",
        "sleep_awake_seconds": "seconds",
        "sleep_average_heart_rate_bpm": "bpm",
        "sleep_average_stress": "Garmin stress score",
        "daily_average_stress": "Garmin stress score",
        "steps": "steps",
        "training_readiness_score": "Garmin score",
    }
    return units[metric_name]


def _latest_recovery_date(export: dict[str, Any], expected_dates: list[str]) -> str | None:
    for date in reversed(expected_dates):
        day = export["days"].get(date)
        if any(metric_value(day, metric) is not None for metric in RECOVERY_METRICS):
            return date
    return None


def _metric_baseline_evidence(
    export: dict[str, Any], metric_name: str, latest_date: str, expected_dates: list[str]
) -> dict[str, Any]:
    latest_value = metric_value(export["days"].get(latest_date), metric_name)
    evidence: dict[str, Any] = {
        "label": _metric_label(metric_name),
        "unit": _metric_unit(metric_name),
        "latest_date": latest_date,
        "latest_value": latest_value,
    }
    if latest_value is None:
        evidence["comparison"] = {
            "available": False,
            "reason": "no measurement for this metric on the latest analysis date",
        }
        return evidence

    prior = [
        (date, metric_value(export["days"].get(date), metric_name))
        for date in expected_dates
        if date < latest_date
    ]
    prior = [(date, value) for date, value in prior if value is not None][-MAX_BASELINE_SAMPLES:]
    values = [value for _, value in prior]
    if len(values) < MIN_BASELINE_SAMPLES:
        evidence["comparison"] = {
            "available": False,
            "reason": "insufficient prior personal measurements",
            "required_samples": MIN_BASELINE_SAMPLES,
            "sample_count": len(values),
        }
        return evidence

    median = _median(values)
    mad = _median([abs(value - median) for value in values])
    delta = latest_value - median
    evidence["comparison"] = {
        "available": True,
        "baseline_window": {"start_date": prior[0][0], "end_date": prior[-1][0]},
        "sample_count": len(values),
        "median": _rounded(median),
        "median_absolute_deviation": _rounded(mad),
        "latest_minus_median": _rounded(delta),
    }
    return evidence


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


def analyze_recovery(payload: Any) -> dict[str, Any]:
    """Compare the latest available recovery evidence with a personal baseline."""
    export = validate_range_export(payload)
    expected_dates = _date_span(export["start_date"], export["end_date"])
    latest_date = _latest_recovery_date(export, expected_dates)
    quality = analyze_data_quality(export)
    if latest_date is None:
        evidence: dict[str, Any] = {}
        comparisons_available = 0
    else:
        evidence = {
            metric: _metric_baseline_evidence(export, metric, latest_date, expected_dates)
            for metric in RECOVERY_METRICS
        }
        comparisons_available = sum(
            item["comparison"]["available"] for item in evidence.values()
        )

    if comparisons_available >= 3:
        confidence = "moderate_personal_baseline_coverage"
    elif comparisons_available:
        confidence = "limited_personal_baseline_coverage"
    else:
        confidence = "insufficient_personal_baseline_coverage"

    limitations = [
        "No composite recovery or medical score is calculated by this analysis.",
        "Garmin sleep HRV Status is a sleep-derived summary, not timestamped beat-to-beat RR data.",
        "Training readiness is Garmin's own multi-factor score and is reported as evidence, not recomputed.",
        "Changes can reflect training, sleep, stress, device wear, or other factors; this output does not establish a cause.",
    ]
    if latest_date is None:
        limitations.append("No supported recovery summary measurement exists in the requested period.")
    elif not comparisons_available:
        limitations.append(
            f"At least {MIN_BASELINE_SAMPLES} prior measurements per metric are required before a personal comparison is emitted."
        )
    return {
        "schema_version": 1,
        "analysis": "recovery",
        "period": {
            "start_date": export["start_date"],
            "end_date": export["end_date"],
            "latest_recovery_date": latest_date,
        },
        "data_quality": {
            "date_coverage_rate": quality["period"]["date_coverage_rate"],
            "metric_sample_counts": quality["metric_sample_counts"],
            "readiness": quality["analysis_readiness"],
        },
        "evidence": evidence,
        "comparisons_available": comparisons_available,
        "confidence": confidence,
        "limitations": limitations,
        "medical_notice": MEDICAL_NOTICE,
    }


def _latest_sleep_date(export: dict[str, Any], expected_dates: list[str]) -> str | None:
    for date in reversed(expected_dates):
        day = export["days"].get(date)
        if any(metric_value(day, metric) is not None for metric in SLEEP_METRICS):
            return date
    return None


def _sleep_schedule_sources(day: Any) -> dict[str, Any]:
    sleep = _mapping(_mapping(day).get("sleep") if _mapping(day) else None)
    dto = _mapping(sleep.get("dailySleepDTO") if sleep else None)
    if dto is None:
        return {}
    result: dict[str, Any] = {}
    for output_name, field_names in (
        ("start", ("sleepStartTimestampLocal", "sleepStartTimestampGMT")),
        ("end", ("sleepEndTimestampLocal", "sleepEndTimestampGMT")),
    ):
        for field_name in field_names:
            value = _value_at(dto, (field_name,))
            if value is not None:
                result[output_name] = {"source_field": field_name, "value": value}
                break
    return result


def _sleep_stage_distribution(day: Any) -> dict[str, Any] | None:
    stage_metrics = {
        "deep": "sleep_deep_seconds",
        "light": "sleep_light_seconds",
        "rem": "sleep_rem_seconds",
    }
    values = {name: metric_value(day, metric) for name, metric in stage_metrics.items()}
    known_values = {name: value for name, value in values.items() if value is not None}
    total = sum(known_values.values())
    if not known_values or total <= 0:
        return None
    return {
        "recorded_stage_seconds": total,
        "stages": {
            name: {
                "seconds": value,
                "proportion_of_recorded_stages": _rounded(value / total),
            }
            for name, value in known_values.items()
        },
    }


def analyze_sleep(payload: Any) -> dict[str, Any]:
    """Describe sleep evidence and personal variation without diagnosing sleep."""
    export = validate_range_export(payload)
    expected_dates = _date_span(export["start_date"], export["end_date"])
    latest_date = _latest_sleep_date(export, expected_dates)
    quality = analyze_data_quality(export)
    if latest_date is None:
        evidence: dict[str, Any] = {}
        stage_distribution = None
        schedule_sources: dict[str, Any] = {}
        comparisons_available = 0
    else:
        evidence = {
            metric: _metric_baseline_evidence(export, metric, latest_date, expected_dates)
            for metric in SLEEP_METRICS
        }
        stage_distribution = _sleep_stage_distribution(export["days"].get(latest_date))
        schedule_sources = _sleep_schedule_sources(export["days"].get(latest_date))
        comparisons_available = sum(
            item["comparison"]["available"] for item in evidence.values()
        )

    if comparisons_available >= 5:
        confidence = "moderate_sleep_baseline_coverage"
    elif comparisons_available:
        confidence = "limited_sleep_baseline_coverage"
    else:
        confidence = "insufficient_sleep_baseline_coverage"
    limitations = [
        "Sleep stage estimates, duration, and derived metrics depend on device wear and Garmin's algorithms.",
        "Stage proportions use only the recorded deep, light, and REM durations; they are not a sleep-disorder assessment.",
        "Sleep schedule source timestamps are retained as supplied and are not converted when the source timezone is unclear.",
        "Garmin sleep HRV Status is a sleep-derived summary, not timestamped beat-to-beat RR data.",
    ]
    if latest_date is None:
        limitations.append("No supported sleep measurements exist in the requested period.")
    elif not comparisons_available:
        limitations.append(
            f"At least {MIN_BASELINE_SAMPLES} prior measurements per metric are required before a personal comparison is emitted."
        )
    return {
        "schema_version": 1,
        "analysis": "sleep",
        "period": {
            "start_date": export["start_date"],
            "end_date": export["end_date"],
            "latest_sleep_date": latest_date,
        },
        "data_quality": {
            "date_coverage_rate": quality["period"]["date_coverage_rate"],
            "metric_sample_counts": quality["metric_sample_counts"],
            "readiness": quality["analysis_readiness"],
        },
        "evidence": evidence,
        "latest_stage_distribution": stage_distribution,
        "latest_schedule_sources": schedule_sources,
        "comparisons_available": comparisons_available,
        "confidence": confidence,
        "limitations": limitations,
        "medical_notice": MEDICAL_NOTICE,
    }


def _body_battery_summary(day: Any, expected_date: str) -> dict[str, Any]:
    """Read only the documented daily Body Battery entry shape.

    ``garminconnect`` documents a one-day response as a list containing one
    mapping, with optional ``charged``, ``drained``, and ``[timestamp, level]``
    observations. Other shapes are deliberately reported as unsupported rather
    than being coerced into a potentially wrong health measurement.
    """
    day_mapping = _mapping(day)
    if day_mapping is None or "body-battery" not in day_mapping:
        return {
            "available": False,
            "reason": "Body Battery endpoint was not included for this date.",
        }
    endpoint = day_mapping["body-battery"]
    if _is_endpoint_error(endpoint):
        return {
            "available": False,
            "reason": "Body Battery endpoint reported an in-band error.",
        }
    if not isinstance(endpoint, list):
        return {
            "available": False,
            "reason": "Unsupported Body Battery response shape; expected a list of daily entries.",
        }
    entries = [item for item in endpoint if isinstance(item, dict)]
    if not entries:
        return {
            "available": False,
            "reason": "Body Battery endpoint returned no daily entry.",
        }
    dated_entries = [entry for entry in entries if entry.get("date") == expected_date]
    if dated_entries:
        entry = dated_entries[0]
    elif len(entries) == 1:
        entry = entries[0]
    else:
        return {
            "available": False,
            "reason": "Body Battery entries cannot be matched unambiguously to this date.",
        }

    charged = _number_at(entry, ("charged",))
    drained = _number_at(entry, ("drained",))
    raw_levels = entry.get("bodyBatteryValuesArray")
    levels: list[int | float] = []
    level_limitations: list[str] = []
    if isinstance(raw_levels, list):
        for row in raw_levels:
            if (
                isinstance(row, list)
                and len(row) == 2
                and not isinstance(row[1], bool)
                and isinstance(row[1], (int, float))
            ):
                levels.append(row[1])
            else:
                level_limitations.append(
                    "An unrecognized Body Battery level row was omitted rather than coerced."
                )
    elif raw_levels is not None:
        return {
            "available": False,
            "reason": "Unsupported Body Battery level series shape; expected [timestamp, level] rows.",
        }
    if charged is None and drained is None and not levels:
        return {
            "available": False,
            "reason": "Body Battery entry has no recognized charged, drained, or level measurement.",
        }

    level_summary = None
    if levels:
        level_summary = {
            "observations": len(levels),
            "first_level": _rounded(levels[0]),
            "last_level": _rounded(levels[-1]),
            "minimum_level": _rounded(min(levels)),
            "maximum_level": _rounded(max(levels)),
        }
    result = {
        "available": True,
        "source_date": entry.get("date") if isinstance(entry.get("date"), str) else expected_date,
        "charged_points": _rounded(charged) if charged is not None else None,
        "drained_points": _rounded(drained) if drained is not None else None,
        "level_summary": level_summary,
    }
    if level_limitations:
        result["limitations"] = list(dict.fromkeys(level_limitations))
    return result


def _body_battery_value(day: Any, date: str, field: str) -> int | float | None:
    summary = _body_battery_summary(day, date)
    if not summary["available"]:
        return None
    value = summary.get(field)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _paired_association(
    expected_dates: list[str],
    left_label: str,
    right_label: str,
    left_value: Any,
    right_value: Any,
) -> dict[str, Any]:
    """Return a descriptive, same-date Pearson correlation when coverage allows."""
    pairs: list[tuple[str, int | float, int | float]] = []
    for date in expected_dates:
        left = left_value(date)
        right = right_value(date)
        if left is not None and right is not None:
            pairs.append((date, left, right))
    if len(pairs) < MIN_BASELINE_SAMPLES:
        return {
            "available": False,
            "left_metric": left_label,
            "right_metric": right_label,
            "paired_days": len(pairs),
            "required_paired_days": MIN_BASELINE_SAMPLES,
            "reason": "insufficient same-date paired measurements",
        }
    left_mean = sum(item[1] for item in pairs) / len(pairs)
    right_mean = sum(item[2] for item in pairs) / len(pairs)
    left_ss = sum((item[1] - left_mean) ** 2 for item in pairs)
    right_ss = sum((item[2] - right_mean) ** 2 for item in pairs)
    if left_ss == 0 or right_ss == 0:
        return {
            "available": False,
            "left_metric": left_label,
            "right_metric": right_label,
            "paired_days": len(pairs),
            "reason": "one paired metric has no variation in the available dates",
        }
    covariance_sum = sum(
        (item[1] - left_mean) * (item[2] - right_mean) for item in pairs
    )
    return {
        "available": True,
        "left_metric": left_label,
        "right_metric": right_label,
        "paired_days": len(pairs),
        "date_window": {"start_date": pairs[0][0], "end_date": pairs[-1][0]},
        "pearson_correlation": _rounded(covariance_sum / (left_ss * right_ss) ** 0.5),
        "scope": "descriptive association between values recorded on the same calendar date; not causal evidence",
    }


def _latest_stress_energy_date(
    export: dict[str, Any], expected_dates: list[str]
) -> str | None:
    for date in reversed(expected_dates):
        day = export["days"].get(date)
        day_mapping = _mapping(day)
        if metric_value(day, "daily_average_stress") is not None:
            return date
        if day_mapping is not None and "body-battery" in day_mapping:
            return date
    return None


def analyze_stress_energy(payload: Any) -> dict[str, Any]:
    """Describe Garmin stress and Body Battery evidence without causal claims."""
    export = validate_range_export(payload)
    expected_dates = _date_span(export["start_date"], export["end_date"])
    latest_date = _latest_stress_energy_date(export, expected_dates)
    quality = analyze_data_quality(export)
    if latest_date is None:
        stress_evidence: dict[str, Any] = {}
        body_battery: dict[str, Any] = {
            "available": False,
            "reason": "No stress or Body Battery endpoint exists in the requested period.",
        }
        latest_context: dict[str, Any] = {}
        associations: dict[str, Any] = {}
        comparisons_available = 0
    else:
        latest_day = export["days"].get(latest_date)
        stress_evidence = _metric_baseline_evidence(
            export, "daily_average_stress", latest_date, expected_dates
        )
        body_battery = _body_battery_summary(latest_day, latest_date)
        latest_context = {
            "sleep_duration_seconds": metric_value(latest_day, "sleep_duration_seconds"),
            "steps": metric_value(latest_day, "steps"),
            "body_battery": body_battery,
        }
        associations = {
            "daily_stress_and_sleep_duration": _paired_association(
                expected_dates,
                "average daily Garmin stress",
                "sleep duration",
                lambda date: metric_value(export["days"].get(date), "daily_average_stress"),
                lambda date: metric_value(export["days"].get(date), "sleep_duration_seconds"),
            ),
            "daily_stress_and_steps": _paired_association(
                expected_dates,
                "average daily Garmin stress",
                "steps",
                lambda date: metric_value(export["days"].get(date), "daily_average_stress"),
                lambda date: metric_value(export["days"].get(date), "steps"),
            ),
            "body_battery_charged_and_sleep_duration": _paired_association(
                expected_dates,
                "Garmin Body Battery charged points",
                "sleep duration",
                lambda date: _body_battery_value(
                    export["days"].get(date), date, "charged_points"
                ),
                lambda date: metric_value(export["days"].get(date), "sleep_duration_seconds"),
            ),
            "body_battery_drained_and_steps": _paired_association(
                expected_dates,
                "Garmin Body Battery drained points",
                "steps",
                lambda date: _body_battery_value(
                    export["days"].get(date), date, "drained_points"
                ),
                lambda date: metric_value(export["days"].get(date), "steps"),
            ),
        }
        comparisons_available = int(stress_evidence["comparison"]["available"])

    association_count = sum(
        item["available"] for item in associations.values()
    )
    if comparisons_available and body_battery["available"] and association_count:
        confidence = "moderate_stress_energy_coverage"
    elif comparisons_available or body_battery["available"] or association_count:
        confidence = "limited_stress_energy_coverage"
    else:
        confidence = "insufficient_stress_energy_coverage"

    limitations = [
        "Garmin stress and Body Battery are device-derived estimates, not clinical measurements.",
        "Same-date associations are descriptive only and do not establish that sleep, activity, stress, or Body Battery caused one another.",
        "Sleep may span calendar dates; the analysis does not infer a timezone or shift measurements between dates.",
        "Body Battery charging and draining values are reported from Garmin and are not recomputed from the level series.",
    ]
    if latest_date is None:
        limitations.append("No supported stress or Body Battery measurement exists in the requested period.")
    elif not body_battery["available"]:
        limitations.append(body_battery["reason"])
    elif body_battery.get("limitations"):
        limitations.extend(body_battery["limitations"])
    if latest_date is not None and not comparisons_available:
        limitations.append(
            f"At least {MIN_BASELINE_SAMPLES} prior daily stress measurements are required before a personal stress comparison is emitted."
        )
    if associations and not association_count:
        limitations.append(
            f"At least {MIN_BASELINE_SAMPLES} same-date paired measurements with variation are required before an association is emitted."
        )
    return {
        "schema_version": 1,
        "analysis": "stress-energy",
        "period": {
            "start_date": export["start_date"],
            "end_date": export["end_date"],
            "latest_stress_energy_date": latest_date,
        },
        "data_quality": {
            "date_coverage_rate": quality["period"]["date_coverage_rate"],
            "metric_sample_counts": quality["metric_sample_counts"],
            "readiness": quality["analysis_readiness"],
        },
        "evidence": {"daily_average_stress": stress_evidence},
        "latest_context": latest_context,
        "same_date_associations": associations,
        "comparisons_available": comparisons_available,
        "confidence": confidence,
        "limitations": limitations,
        "medical_notice": MEDICAL_NOTICE,
    }


def analyze_export(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "data-quality":
        return analyze_data_quality(payload)
    if kind == "recovery":
        return analyze_recovery(payload)
    if kind == "sleep":
        return analyze_sleep(payload)
    if kind == "stress-energy":
        return analyze_stress_energy(payload)
    raise AnalysisInputError(f"unsupported analysis kind: {kind}")
