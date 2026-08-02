import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from garmin_health_analysis import (
    AnalysisInputError,
    analyze_data_quality,
    analyze_recovery,
    analyze_sleep,
    analyze_stress_energy,
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

    def test_sleep_reports_stage_distribution_and_duration_baseline(self):
        days = {}
        for day in range(1, 8):
            payload = daily_payload(day)
            payload["sleep"]["dailySleepDTO"].update(
                {
                    "sleepTimeSeconds": 25200,
                    "deepSleepSeconds": 5400,
                    "lightSleepSeconds": 12600,
                    "remSleepSeconds": 7200,
                    "awakeSleepSeconds": 1200,
                    "sleepStartTimestampLocal": f"2026-08-{day:02d}T23:00:00",
                    "sleepEndTimestampLocal": f"2026-08-{day + 1:02d}T06:00:00",
                }
            )
            days[f"2026-08-{day:02d}"] = payload
        latest = daily_payload(8)
        latest["sleep"]["dailySleepDTO"].update(
            {
                "sleepTimeSeconds": 23400,
                "deepSleepSeconds": 4500,
                "lightSleepSeconds": 11700,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 900,
                "sleepStartTimestampLocal": "2026-08-08T23:30:00",
                "sleepEndTimestampLocal": "2026-08-09T06:00:00",
            }
        )
        days["2026-08-08"] = latest
        payload = range_export(days)
        payload["end_date"] = "2026-08-08"

        result = analyze_sleep(payload)

        duration = result["evidence"]["sleep_duration_seconds"]
        stages = result["latest_stage_distribution"]
        self.assertEqual(result["period"]["latest_sleep_date"], "2026-08-08")
        self.assertEqual(duration["comparison"]["median"], 25200)
        self.assertEqual(duration["comparison"]["latest_minus_median"], -1800)
        self.assertEqual(stages["recorded_stage_seconds"], 23400)
        self.assertEqual(stages["stages"]["deep"]["proportion_of_recorded_stages"], 0.1923)
        self.assertEqual(
            result["latest_schedule_sources"]["start"]["source_field"],
            "sleepStartTimestampLocal",
        )

    def test_sleep_preserves_zero_measurements_without_inventing_stage_distribution(self):
        payload = range_export(
            {
                "2026-08-01": {
                    "sleep": {
                        "dailySleepDTO": {
                            "sleepTimeSeconds": 0,
                            "deepSleepSeconds": 0,
                            "lightSleepSeconds": 0,
                            "remSleepSeconds": 0,
                            "awakeSleepSeconds": 0,
                        }
                    }
                }
            }
        )
        payload["end_date"] = "2026-08-01"

        result = analyze_sleep(payload)

        self.assertEqual(result["evidence"]["sleep_awake_seconds"]["latest_value"], 0)
        self.assertIsNone(result["latest_stage_distribution"])
        self.assertEqual(result["comparisons_available"], 0)

    def test_sleep_reports_missing_measurements_as_a_limitation(self):
        payload = range_export({"2026-08-01": {"sleep": {}}})
        payload["end_date"] = "2026-08-01"

        result = analyze_sleep(payload)

        self.assertIsNone(result["period"]["latest_sleep_date"])
        self.assertEqual(result["evidence"], {})
        self.assertIn("No supported sleep measurements", result["limitations"][-1])

    def test_stress_energy_reports_garmin_body_battery_and_descriptive_pairings(self):
        days = {}
        for day in range(1, 9):
            payload = daily_payload(day)
            payload["stats"]["totalSteps"] = day * 1000
            payload["stress"]["avgStressLevel"] = 20 + day
            payload["sleep"]["dailySleepDTO"]["sleepTimeSeconds"] = 21600 + day * 600
            payload["body-battery"] = [
                {
                    "date": f"2026-08-{day:02d}",
                    "charged": day * 4,
                    "drained": day * 6,
                    "bodyBatteryValuesArray": [[1, 70], [2, 65], [3, 68]],
                }
            ]
            days[f"2026-08-{day:02d}"] = payload
        payload = range_export(days)
        payload["end_date"] = "2026-08-08"
        payload["kinds"].append("body-battery")

        result = analyze_stress_energy(payload)

        body_battery = result["latest_context"]["body_battery"]
        stress = result["evidence"]["daily_average_stress"]
        self.assertEqual(result["period"]["latest_stress_energy_date"], "2026-08-08")
        self.assertTrue(body_battery["available"])
        self.assertEqual(body_battery["charged_points"], 32)
        self.assertEqual(body_battery["drained_points"], 48)
        self.assertEqual(body_battery["level_summary"]["minimum_level"], 65)
        self.assertTrue(stress["comparison"]["available"])
        association = result["same_date_associations"]["daily_stress_and_steps"]
        self.assertTrue(association["available"])
        self.assertEqual(association["paired_days"], 8)
        self.assertEqual(association["pearson_correlation"], 1)
        self.assertIn("not causal", association["scope"])

    def test_stress_energy_preserves_unsupported_body_battery_shape_as_limitation(self):
        payload = range_export(
            {
                "2026-08-01": {
                    "stress": {"avgStressLevel": 31},
                    "body-battery": {"charged": 10, "drained": 20},
                }
            }
        )
        payload["end_date"] = "2026-08-01"
        payload["kinds"].append("body-battery")

        result = analyze_stress_energy(payload)

        body_battery = result["latest_context"]["body_battery"]
        self.assertFalse(body_battery["available"])
        self.assertIn("Unsupported Body Battery response shape", body_battery["reason"])
        self.assertIn(body_battery["reason"], result["limitations"])

    def test_stress_energy_does_not_coerce_malformed_body_battery_level_rows(self):
        payload = range_export(
            {
                "2026-08-01": {
                    "stress": {"avgStressLevel": 31},
                    "body-battery": [
                        {
                            "date": "2026-08-01",
                            "charged": 0,
                            "drained": 12,
                            "bodyBatteryValuesArray": [[1, {"unknown": "level"}]],
                        }
                    ],
                }
            }
        )
        payload["end_date"] = "2026-08-01"
        payload["kinds"].append("body-battery")

        result = analyze_stress_energy(payload)

        body_battery = result["latest_context"]["body_battery"]
        self.assertTrue(body_battery["available"])
        self.assertEqual(body_battery["charged_points"], 0)
        self.assertIsNone(body_battery["level_summary"])
        self.assertIn("omitted rather than coerced", body_battery["limitations"][0])
        self.assertIn(body_battery["limitations"][0], result["limitations"])


if __name__ == "__main__":
    unittest.main()
