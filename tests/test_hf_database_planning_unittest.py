import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pandas as pd


# The planning helpers are pure, but they live in the operational runner, whose
# optional runtime dependencies are not installed in every unit-test environment.
# Install narrow import stubs only when those packages are absent; production
# environments continue to use the real dependencies.
if importlib.util.find_spec("psycopg2") is None:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_extras_stub = types.ModuleType("psycopg2.extras")

    def _execute_values_stub(*_args, **_kwargs):
        raise AssertionError("execute_values is not used by HF planning tests")

    psycopg2_extras_stub.execute_values = _execute_values_stub
    psycopg2_stub.extras = psycopg2_extras_stub
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = psycopg2_extras_stub

if importlib.util.find_spec("utide") is None:
    utide_stub = types.ModuleType("utide")

    def _utide_stub(*_args, **_kwargs):
        raise AssertionError("UTide is not used by HF planning tests")

    utide_stub.solve = _utide_stub
    utide_stub.reconstruct = _utide_stub
    sys.modules["utide"] = utide_stub

from scripts import run_station_datums_predictions as runner


class _FakeCursor:
    def __init__(self):
        self.executions = []
        self.copy_calls = []
        self.rowcount = 3

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def copy_expert(self, sql, file):
        self.copy_calls.append((sql, file.read()))


class _FakeConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class HfDatabasePlanningTests(unittest.TestCase):
    @staticmethod
    def _epoch_sync(*, station_kind: str, epoch_name: str) -> runner.EpochSyncResult:
        return runner.EpochSyncResult(
            record_id="007" if station_kind == "FD" else "007a",
            station_kind=station_kind,
            source_record_id="007" if station_kind == "FD" else "007a",
            input_basis_code=(
                "best_available" if station_kind == "FD" else "research_quality"
            ),
            input_basis_id=1 if station_kind == "FD" else 2,
            time_series_id=100 if station_kind == "FD" else 101,
            id_from_source="000007" if station_kind == "FD" else "000007A",
            resolution_rule="test",
            epoch_id_by_name={epoch_name: 500},
            write_epochs=True,
        )

    @staticmethod
    def _record(
        *,
        station_kind: str,
        epoch_name: str,
        start: str,
        end: str,
    ) -> dict:
        times = pd.date_range(start, end, freq="1h")
        values = np.sin(np.arange(len(times), dtype=float) / 6.0)
        return {
            "record_id": "007" if station_kind == "FD" else "007a",
            "station_kind": station_kind,
            "hourly_predictions": [
                {
                    "epoch": epoch_name,
                    "prediction_key": "primary",
                    "start": times[0],
                    "end": times[-1],
                }
            ],
            "_hourly_prediction_frames": {
                "primary": pd.DataFrame(
                    {"time": times, "prediction_mm": values}
                )
            },
        }

    def test_fd_plan_intersects_configured_hf_window(self):
        epoch_name = "RECENT_2015_2017"
        record = self._record(
            station_kind="FD",
            epoch_name=epoch_name,
            start="2015-12-31 00:00:00",
            end="2016-01-03 00:00:00",
        )
        epoch_sync = self._epoch_sync(
            station_kind="FD",
            epoch_name=epoch_name,
        )
        prediction_window = runner.PredictionDbWindow(
            resolution_id=1,
            temporal_resolution_code="hourly",
            record_quality_short_name=None,
            date_begin=None,
            date_end=None,
            date_range_last_update=None,
        )
        resolution_target = runner.HfResolutionTarget(
            resolution_id=2,
            temporal_resolution_code="5-minute",
            latest_time=pd.Timestamp("2016-01-02"),
        )
        policy = replace(
            runner.DATABASE_POLICY,
            hf_prediction_start=pd.Timestamp("2016-01-01 00:00:00"),
            hf_prediction_end=pd.Timestamp("2016-01-02 23:00:00"),
        )

        with patch.object(runner, "DATABASE_POLICY", policy):
            plans, _notes = runner._build_hf_tide_prediction_plans(
                record=record,
                epoch_sync=epoch_sync,
                prediction_window=prediction_window,
                resolution_target=resolution_target,
            )

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].insert_start, pd.Timestamp("2016-01-01 00:00:00"))
        self.assertEqual(plans[0].insert_end, pd.Timestamp("2016-01-02 23:00:00"))
        self.assertEqual(plans[0].rows_planned, 565)

    def test_hf_writes_require_regular_tide_prediction_writes(self):
        policy = replace(
            runner.DATABASE_POLICY,
            write_epochs=True,
            write_datums=True,
            write_constituents=True,
            write_tide_predictions=False,
            write_hf_tide_predictions=True,
        )

        with patch.object(runner, "DATABASE_POLICY", policy):
            with self.assertRaisesRegex(
                RuntimeError,
                "write_hf_tide_predictions=True requires",
            ):
                runner._validate_database_write_policy_or_die()

    def test_old_rq_product_with_no_hf_overlap_plans_no_inserts(self):
        epoch_name = "RECENT_1970_1972"
        record = self._record(
            station_kind="RQ",
            epoch_name=epoch_name,
            start="1970-01-01 00:00:00",
            end="1972-12-31 23:00:00",
        )
        epoch_sync = self._epoch_sync(
            station_kind="RQ",
            epoch_name=epoch_name,
        )
        prediction_window = runner.PredictionDbWindow(
            resolution_id=1,
            temporal_resolution_code="hourly",
            record_quality_short_name="rq",
            date_begin=pd.Timestamp("1970-01-01 00:00:00"),
            date_end=pd.Timestamp("1972-12-31 23:00:00"),
            date_range_last_update=pd.Timestamp("2026-01-01 00:00:00"),
        )
        plans, notes = runner._build_hf_tide_prediction_plans(
            record=record,
            epoch_sync=epoch_sync,
            prediction_window=prediction_window,
            resolution_target=None,
        )

        self.assertEqual(len(plans), 1)
        self.assertIsNone(plans[0].resolution_id)
        self.assertIsNone(plans[0].interval)
        self.assertIsNone(plans[0].insert_start)
        self.assertIsNone(plans[0].insert_end)
        self.assertEqual(plans[0].rows_planned, 0)
        self.assertTrue(any("No HF overlap" in note for note in notes))

    def test_old_rq_no_overlap_reconciles_without_resolution_lookup(self):
        epoch_name = "RECENT_1970_1972"
        record = self._record(
            station_kind="RQ",
            epoch_name=epoch_name,
            start="1970-01-01 00:00:00",
            end="1972-12-31 23:00:00",
        )
        epoch_sync = self._epoch_sync(
            station_kind="RQ",
            epoch_name=epoch_name,
        )
        prediction_window = runner.PredictionDbWindow(
            resolution_id=1,
            temporal_resolution_code="hourly",
            record_quality_short_name="rq",
            date_begin=pd.Timestamp("1970-01-01 00:00:00"),
            date_end=pd.Timestamp("1972-12-31 23:00:00"),
            date_range_last_update=pd.Timestamp("2026-01-01 00:00:00"),
        )
        connection = _FakeConnection()

        @contextmanager
        def fake_tsdb_connection():
            yield connection, object()

        policy = replace(
            runner.DATABASE_POLICY,
            write_hf_tide_predictions=True,
        )

        with (
            patch.object(runner, "DATABASE_POLICY", policy),
            patch.object(runner, "_tsdb_connection", fake_tsdb_connection),
            patch.object(runner, "_query_hf_resolution_target") as resolution_query,
            patch.object(runner, "log"),
        ):
            result = runner._sync_record_hf_tide_predictions_to_database(
                record,
                epoch_sync=epoch_sync,
                prediction_window=prediction_window,
            )

        resolution_query.assert_not_called()
        self.assertEqual(result.rows_planned, 0)
        self.assertEqual(result.rows_written, 0)
        self.assertEqual(result.rows_deleted, 3)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)

    def test_hf_replacement_deletes_all_resolution_variants(self):
        hourly = pd.DataFrame(
            {
                "time": pd.date_range("2016-01-01", periods=2, freq="1h"),
                "prediction_mm": [0.0, 1.0],
            }
        )
        plan = runner.HfTidePredictionPlan(
            epoch_name="RECENT_2015_2017",
            epoch_id=500,
            time_series_id=100,
            resolution_id=2,
            temporal_resolution_code="5-minute",
            interval=pd.Timedelta(minutes=5),
            delete_start=pd.Timestamp("2016-01-01"),
            delete_end=pd.Timestamp("2016-01-02"),
            insert_start=None,
            insert_end=None,
            rows_planned=0,
            hourly_frame=hourly,
        )
        connection = _FakeConnection()

        deleted = runner._delete_hf_tide_prediction_windows(connection, [plan])

        self.assertEqual(deleted, 3)
        sql, params = connection.cursor_instance.executions[0]
        self.assertNotIn("AND resolution_id", sql)
        self.assertEqual(len(params), 4)

    def test_hf_copy_serializes_database_columns(self):
        cursor = _FakeCursor()
        chunk = pd.DataFrame(
            {
                "time": [pd.Timestamp("2016-01-01 00:00:00")],
                "value": [1.2345],
                "epoch_id": [500],
                "resolution_id": [2],
                "time_series_id": [100],
            }
        )

        written = runner._copy_hf_prediction_chunk(cursor, chunk)

        self.assertEqual(written, 1)
        sql, payload = cursor.copy_calls[0]
        self.assertIn("COPY public.hf_tide_prediction", sql)
        self.assertEqual(payload, "2016-01-01 00:00:00,1.2345,500,2,100\n")

    def test_latest_hf_resolution_ambiguity_is_rejected(self):
        rows = [
            {
                "resolution_id": 2,
                "temporal_resolution_code": "5-minute",
                "latest_time": pd.Timestamp("2026-01-01"),
            },
            {
                "resolution_id": 3,
                "temporal_resolution_code": "1-minute",
                "latest_time": pd.Timestamp("2026-01-01"),
            },
        ]

        with patch.object(runner, "_fetchall_dicts", return_value=rows):
            with self.assertRaisesRegex(RuntimeError, "Multiple HF resolutions"):
                runner._query_hf_resolution_target(
                    object(),
                    time_series_id=100,
                )

    def test_best_available_cleanup_includes_hf_predictions(self):
        connection = _FakeConnection()
        deleted = runner._delete_predictions_outside_best_available_windows(
            connection,
            actions=[
                {
                    "time_series_id": 100,
                    "valid_start": pd.Timestamp("2016-01-01 00:00:00"),
                    "valid_end": pd.Timestamp("2020-12-31 23:00:00"),
                }
            ],
            best_available_input_basis_id=1,
            hourly_resolution_id=10,
        )

        self.assertEqual(deleted, (6, 6, 6))
        sql_statements = [
            sql for sql, _params in connection.cursor_instance.executions
        ]
        self.assertEqual(len(sql_statements), 6)
        self.assertEqual(
            sum("public.hf_tide_prediction" in sql for sql in sql_statements),
            2,
        )

    def test_stale_recent_epoch_cleanup_includes_hf_predictions(self):
        connection = _FakeConnection()
        epoch_sync = self._epoch_sync(
            station_kind="FD",
            epoch_name="RECENT_2015_2017",
        )

        deleted = runner._delete_stale_recent_epochs(
            connection,
            epoch_sync=epoch_sync,
            stale_epoch_ids=[500],
        )

        self.assertEqual(deleted["hf_tide_prediction_rows_deleted"], 3)
        sql_statements = [
            sql for sql, _params in connection.cursor_instance.executions
        ]
        self.assertEqual(
            sum("public.hf_tide_prediction" in sql for sql in sql_statements),
            1,
        )


if __name__ == "__main__":
    unittest.main()
