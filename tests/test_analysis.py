import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from garmin_health_analysis import (
    AnalysisInputError,
    analyze_data_quality,
    analyze_recovery,
    validate_range_export,
)


def daily_payload(day: int) -> dict:
    return {
        "stats": {"restingHeartRate": 50 + day, "totalSteps": 5000 + day},
        "hrv": {"hrvSummary": {"lastNightAvg": 48 + day, "weeklyAvg": 50}},
        "sleep": {
            "dailySleepDTO": {
                "sleepTimeSeconds": 25200,
                "avgHeartRate": 52,
                "avgSleepStress": 18,
            }
        },
        "stress": {"avgStressLevel": 31},
        "training-readiness": [{"score": 72}],
    }


def range_export(days: dict) -> dict:
    return {
        "schema_version": 1,
        "type": "garmin-health-range",
        "start_date": "2026-08-01",
        "end_date": "2026-08-07",
        "kinds": ["stats", "hrv", "sleep", "stress", "training-readiness"],
        "days": days,
    }


class DataQualityAnalysisTests(unittest.TestCase):
    def test_complete_range_reports_metric_coverage(self):
        payload = range_export(
            {f"2026-08-0{day}": daily_payload(day) for day in range(1, 8)}
        )

        result = analyze_data_quality(payload)

        self.assertEqual(result["analysis"], "data-quality")
        self.assertEqual(result["period"]["date_coverage_rate"], 1.0)
        self.assertEqual(result["endpoints"]["hrv"]["days_available"], 7)
        self.assertEqual(result["metric_sample_counts"]["hrv_last_night_ms"], 7)
        self.assertEqual(result["analysis_readiness"], "ready_for_descriptive_trends")
        self.assertIn("not a medical diagnosis", result["medical_notice"])

    def test_missing_days_and_endpoint_errors_are_not_counted_as_measurements(self):
        payload = range_export(
            {
                "2026-08-01": {
                    "stats": {"restingHeartRate": 52},
                    "hrv": {
                        "available": False,
                        "error": {"type": "RuntimeError", "message": "unsupported"},
                    },
                },
                "2026-08-03": {"stats": {"restingHeartRate": 53}},
            }
        )
        payload["end_date"] = "2026-08-03"

        result = analyze_data_quality(payload)

        self.assertEqual(result["period"]["missing_dates"], ["2026-08-02"])
        self.assertEqual(result["endpoints"]["hrv"]["days_with_endpoint_error"], 1)
        self.assertEqual(result["endpoints"]["hrv"]["missing_dates"], ["2026-08-02", "2026-08-03"])
        self.assertEqual(result["metric_sample_counts"]["hrv_last_night_ms"], 0)
        self.assertEqual(result["analysis_readiness"], "insufficient_for_7_day_descriptive_trends")

    def test_rejects_non_range_input(self):
        with self.assertRaises(AnalysisInputError):
            validate_range_export({"type": "not-a-range", "days": {}})

    def test_recovery_compares_latest_day_with_prior_robust_baseline(self):
        days = {}
        for day in range(1, 8):
            payload = daily_payload(day)
            payload["stats"]["restingHeartRate"] = 52
            payload["hrv"]["hrvSummary"]["lastNightAvg"] = 50
            payload["sleep"]["dailySleepDTO"]["sleepTimeSeconds"] = 25200
            payload["training-readiness"] = [{"score": 70}]
            days[f"2026-08-{day:02d}"] = payload
        latest = daily_payload(8)
        latest["stats"]["restingHeartRate"] = 58
        latest["hrv"]["hrvSummary"]["lastNightAvg"] = 45
        latest["sleep"]["dailySleepDTO"]["sleepTimeSeconds"] = 23400
        latest["training-readiness"] = [{"score": 0}]
        days["2026-08-08"] = latest
        payload = range_export(days)
        payload["end_date"] = "2026-08-08"

        result = analyze_recovery(payload)

        hrv = result["evidence"]["hrv_last_night_ms"]
        self.assertEqual(result["period"]["latest_recovery_date"], "2026-08-08")
        self.assertTrue(hrv["comparison"]["available"])
        self.assertEqual(hrv["comparison"]["sample_count"], 7)
        self.assertEqual(hrv["comparison"]["median"], 50)
        self.assertEqual(hrv["comparison"]["latest_minus_median"], -5)
        self.assertEqual(
            result["evidence"]["training_readiness_score"]["latest_value"], 0
        )
        self.assertEqual(result["comparisons_available"], 4)
        self.assertEqual(result["confidence"], "moderate_personal_baseline_coverage")

    def test_recovery_suppresses_comparison_without_seven_prior_samples(self):
        payload = range_export(
            {f"2026-08-0{day}": daily_payload(day) for day in range(1, 8)}
        )

        result = analyze_recovery(payload)

        comparison = result["evidence"]["hrv_last_night_ms"]["comparison"]
        self.assertFalse(comparison["available"])
        self.assertEqual(comparison["sample_count"], 6)
        self.assertNotIn("median", comparison)
        self.assertEqual(result["comparisons_available"], 0)
        self.assertEqual(result["confidence"], "insufficient_personal_baseline_coverage")


if __name__ == "__main__":
    unittest.main()
