import contextlib
import importlib.util
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CLI_PATH = Path(__file__).resolve().parents[1] / "garmin_health_cli.py"
SPEC = importlib.util.spec_from_file_location("garmin_health_cli", CLI_PATH)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class SecureOutputTests(unittest.TestCase):
    def test_writes_atomic_owner_only_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "export.json"
            cli.write_secure_json(path, {"value": 1}, force=False)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                cli.write_secure_json(path, {"value": 2}, force=False)

            cli.write_secure_json(path, {"value": 2}, force=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": 2})


class CommandTests(unittest.TestCase):
    def test_series_returns_consistent_error_envelope(self):
        args = SimpleNamespace(
            date="2026-08-01",
            kind="hrv",
            timezone="+08:00",
            tokenstore=Path("/unused"),
        )
        with patch.object(cli, "get_client", side_effect=RuntimeError("expired token")):
            result = cli.cmd_series(args)

        self.assertFalse(result["available"])
        self.assertEqual(result["error"]["type"], "RuntimeError")
        self.assertEqual(result["semantics"]["unit"], "milliseconds as provided by Garmin")

    def test_range_export_checkpoints_each_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "range.json"
            args = SimpleNamespace(
                stdout=False,
                output=str(output),
                resume=False,
                force=False,
                all_kinds=False,
                kind=["stats"],
                start_date="2026-08-01",
                end_date="2026-08-02",
                delay=0,
                tokenstore=Path("/unused"),
            )

            def getters(_client, date):
                return {"stats": lambda: {"source_date": date}}

            with patch.object(cli, "get_client", return_value=object()), patch.object(
                cli, "daily_getters", side_effect=getters
            ):
                result = cli.cmd_export_range(args)

            exported = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["days_fetched"], 2)
            self.assertEqual(
                exported["days"]["2026-08-02"]["stats"]["source_date"], "2026-08-02"
            )
            self.assertTrue(args._output_already_written)

            resume_args = SimpleNamespace(
                stdout=False,
                output=str(output),
                resume=True,
                force=False,
                all_kinds=False,
                kind=None,
                start_date="2026-08-01",
                end_date="2026-08-02",
                delay=0,
                tokenstore=Path("/unused"),
            )
            with patch.object(cli, "get_client", side_effect=AssertionError("no fetch needed")):
                resumed = cli.cmd_export_range(resume_args)
            self.assertEqual(resumed["days_fetched"], 0)
            self.assertEqual(resumed["days_skipped"], 2)

    def test_parser_rejects_non_positive_stream_limit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["activity-stream", "1", "--max-chart", "0"])

    def test_unknown_fit_field_keeps_definition_and_raw_value(self):
        field = SimpleNamespace(
            name="unknown_127",
            def_num=127,
            base_type=SimpleNamespace(name="uint16"),
            field_def=SimpleNamespace(is_dev=False),
            units=None,
            value=42,
            raw_value=42,
        )
        metadata = cli.fit_unknown_field_metadata(field)
        self.assertEqual(metadata["field_number"], 127)
        self.assertEqual(metadata["base_type"], "uint16")
        self.assertEqual(metadata["raw_value"], 42)


if __name__ == "__main__":
    unittest.main()
