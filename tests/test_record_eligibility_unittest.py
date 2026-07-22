import importlib.util
import json
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


if "psycopg2" not in sys.modules and importlib.util.find_spec("psycopg2") is None:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_extras_stub = types.ModuleType("psycopg2.extras")

    def _execute_values_stub(*_args, **_kwargs):
        raise AssertionError(
            "execute_values is not used by record eligibility tests"
        )

    psycopg2_extras_stub.execute_values = _execute_values_stub
    psycopg2_stub.extras = psycopg2_extras_stub
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = psycopg2_extras_stub


if "utide" not in sys.modules and importlib.util.find_spec("utide") is None:
    utide_stub = types.ModuleType("utide")

    def _utide_stub(*_args, **_kwargs):
        raise AssertionError(
            "UTide is not used by record eligibility tests"
        )

    utide_stub.solve = _utide_stub
    utide_stub.reconstruct = _utide_stub
    sys.modules["utide"] = utide_stub


from scripts import run_station_datums_predictions as runner


class RecordEligibilityTests(unittest.TestCase):
    @staticmethod
    def _minimal_fd_record(station_id: str) -> dict:
        start = pd.Timestamp("2025-01-01 00:00:00")
        end = pd.Timestamp("2025-01-01 01:00:00")

        hourly = {
            "epoch": "RECENT_2025-01-01_2025-01-01",
            "prediction_key": "primary",
            "variable": "hourly_prediction_primary",
            "start": start,
            "end": end,
            "rows": 2,
            "is_prediction_basis": True,
        }

        return {
            "record_id": station_id,
            "station_name": "Test Station",
            "station_kind": "FD",
            "netcdf": f"{station_id}.nc",
            "prediction_basis_epoch": hourly["epoch"],
            "prediction_scope": "test",
            "hourly_prediction": hourly,
            "hourly_predictions": [hourly],
            "minute_highlow_prediction": {"saved": False},
            "minute_highlow_predictions": [],
            "update_cycle_months": 3,
            "update_cycle_reason": "test",
            "epochs": [],
            "_hourly_prediction_frames": {},
            "_minute_highlow_frames": {},
        }

    def test_run_record_raises_named_error_with_eligibility_diagnostics(self):
        raw = pd.DataFrame(
            {
                "station_name": ["Test Station"] * 4,
                "time": pd.date_range(
                    "2025-01-01",
                    periods=4,
                    freq="1h",
                ),
                "sea_level": [1.0, None, 2.0, None],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(
                    runner,
                    "get_station_metadata",
                    return_value=SimpleNamespace(latitude=0.0),
                ),
                patch.object(
                    runner,
                    "get_station_switch_levels",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "fetch_rq_hourly",
                    return_value=raw,
                ),
                patch.object(
                    runner,
                    "select_epochs",
                    return_value=[],
                ),
                patch.object(runner, "log"),
            ):
                with self.assertRaises(
                    runner.NoQualifyingEpochError
                ) as caught:
                    runner._run_record(
                        "431",
                        "RQ",
                        Path(tmpdir),
                        version="A",
                    )

        message = str(caught.exception)

        self.assertIn(
            "No qualifying epochs found for 431a",
            message,
        )
        self.assertIn("total_rows=4", message)
        self.assertIn("valid_rows=2", message)
        self.assertIn(
            "minimum_completion_fraction=0.75",
            message,
        )
        self.assertIn("minimum_recent_months=3", message)

    def test_warn_mode_skips_ineligible_rq_record_and_finishes_station(self):
        station_id = "431"
        fd_record = self._minimal_fd_record(station_id)

        ineligible = runner.NoQualifyingEpochError(
            "No qualifying epochs found for 431a: test diagnostics"
        )

        policy = replace(
            runner.DATABASE_POLICY,
            reconciliation_mode="warn",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            fake_script_path = (
                base
                / "scripts"
                / "run_station_datums_predictions.py"
            )

            with (
                patch.object(runner, "DATABASE_POLICY", policy),
                patch.object(
                    runner,
                    "__file__",
                    str(fake_script_path),
                ),
                patch.object(
                    runner,
                    "list_rq_versions",
                    return_value={station_id: ["A"]},
                ),
                patch.object(
                    runner,
                    "_build_station_source_reconciliation",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_record",
                    side_effect=[fd_record, ineligible],
                ),
                patch.object(
                    runner,
                    "_sync_record_core_tables_to_database",
                    return_value=(None, None, None),
                ),
                patch.object(
                    runner,
                    "_resolve_record_prediction_db_window",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_tide_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_hf_tide_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_high_low_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_stale_recent_epoch_cleanup",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_auto_best_available_prediction_cleanup",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_prediction_cutover_cleanup",
                ),
                patch.object(runner, "log"),
            ):
                runner._run_station(station_id)

            summary_path = (
                base
                / "artifacts"
                / f"station{station_id}"
                / "summary.json"
            )
            summary = json.loads(summary_path.read_text())

        self.assertEqual(
            [
                record["record_id"]
                for record in summary["records"]
            ],
            ["431"],
        )

        self.assertEqual(
            len(summary["skipped_records"]),
            1,
        )

        skipped = summary["skipped_records"][0]

        self.assertEqual(skipped["record_id"], "431a")
        self.assertEqual(
            skipped["error_type"],
            "NoQualifyingEpochError",
        )
        self.assertIn(
            "duration/completion",
            skipped["reason"],
        )

    def test_strict_mode_rejects_ineligible_rq_record(self):
        station_id = "431"
        fd_record = self._minimal_fd_record(station_id)

        ineligible = runner.NoQualifyingEpochError(
            "No qualifying epochs found for 431a: test diagnostics"
        )

        policy = replace(
            runner.DATABASE_POLICY,
            reconciliation_mode="strict",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            fake_script_path = (
                base
                / "scripts"
                / "run_station_datums_predictions.py"
            )

            with (
                patch.object(runner, "DATABASE_POLICY", policy),
                patch.object(
                    runner,
                    "__file__",
                    str(fake_script_path),
                ),
                patch.object(
                    runner,
                    "list_rq_versions",
                    return_value={station_id: ["A"]},
                ),
                patch.object(
                    runner,
                    "_build_station_source_reconciliation",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_record",
                    side_effect=[fd_record, ineligible],
                ),
                patch.object(
                    runner,
                    "_sync_record_core_tables_to_database",
                    return_value=(None, None, None),
                ),
                patch.object(
                    runner,
                    "_resolve_record_prediction_db_window",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_tide_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_hf_tide_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_sync_record_high_low_predictions_to_database",
                    return_value=None,
                ),
                patch.object(
                    runner,
                    "_run_stale_recent_epoch_cleanup",
                    return_value=None,
                ),
                patch.object(runner, "log"),
            ):
                with self.assertRaises(
                    runner.NoQualifyingEpochError
                ):
                    runner._run_station(station_id)


if __name__ == "__main__":
    unittest.main()
