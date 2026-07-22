from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from io import StringIO
from functools import lru_cache
from pathlib import Path
import sys
import gc
import os
import re
from typing import Any
from psycopg2.extras import execute_values

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidal_config import (
    DATABASE_POLICY,
    EPOCH_POLICY,
    HAT_LAT_PREDICTION_END,
    HAT_LAT_PREDICTION_START,
)

from hf_tide_predictions import (
    hf_prediction_row_count,
    iter_hf_prediction_chunks,
    minute_resolution_timedelta,
)

from core import (
    build_prediction_save_plan,
    build_netcdf_dataset,
    clean_hourly_dataframe,
    compute_datums,
    ErddapNoRowsError,
    ErddapUnavailableError,
    fetch_fd_hourly,
    fetch_rq_hourly,
    fit_harmonics,
    prepare_harmonic_fit_dataframe,
    load_harmonic_result,
    predict_minute_high_low,
    predict_from_harmonics,
    saved_minute_highlow_epoch_items,
    saved_prediction_epoch_items,
    save_harmonic_result,
    save_netcdf,
    select_epochs,
    strip_harmonic_result,
    fetch_station_metadata_index,
    get_station_metadata,
    get_station_switch_levels,
    list_rq_versions,
)


DEFAULT_STATION_ID = "007"


class NoQualifyingEpochError(RuntimeError):
    """Raised when source rows exist but no configured epoch is eligible."""


@dataclass(frozen=True)
class DBTimeSeriesRow:
    id: int
    id_from_source: str


@dataclass(frozen=True)
class EpochWriteTarget:
    source_record_id: str
    input_basis_code: str
    input_basis_id: int
    time_series_id: int
    id_from_source: str
    resolution_rule: str


@dataclass(frozen=True)
class EpochSyncResult:
    """Database write manifest produced by the epoch sync step.

    Future datum, constituent, and tide_prediction DB writers should consume
    this object instead of independently resolving station/version identity.
    """

    record_id: str
    station_kind: str
    source_record_id: str
    input_basis_code: str
    input_basis_id: int
    time_series_id: int
    id_from_source: str
    resolution_rule: str
    epoch_id_by_name: dict[str, int]
    write_epochs: bool


@dataclass(frozen=True)
class DatumSyncResult:
    """Database write manifest produced by the datum sync step.

    Datum sync must consume EpochSyncResult and must not independently resolve
    station/version identity.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    datum_id_by_epoch_and_short_name: dict[str, dict[str, int]]
    rows_planned: int
    rows_written: int
    write_datums: bool


@dataclass(frozen=True)
class ConstituentSyncResult:
    """Database write manifest produced by the constituent sync step.

    Constituent sync must consume EpochSyncResult and must not independently
    resolve station/version identity.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    constituent_id_by_epoch_and_short_name: dict[str, dict[str, int]]
    missing_definition_short_names: list[str]
    rows_planned: int
    rows_written: int
    write_constituents: bool


@dataclass(frozen=True)
class TidePredictionSyncResult:
    """Database write manifest produced by hourly tide prediction sync.

    Tide prediction sync must consume EpochSyncResult and must not independently
    resolve station/version identity.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    resolution_id: int
    rows_planned: int
    rows_deleted: int
    rows_written: int
    rows_skipped: int
    write_tide_predictions: bool
    notes: list[str]


@dataclass(frozen=True)
class HfTidePredictionSyncResult:
    """Database write manifest produced by HF tide prediction sync.

    HF prediction sync consumes the same saved hourly prediction products and
    EpochSyncResult used by regular tide-prediction synchronization.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    resolution_id: int | None
    temporal_resolution_code: str | None
    rows_planned: int
    rows_deleted: int
    rows_written: int
    rows_skipped: int
    write_hf_tide_predictions: bool
    notes: list[str]


@dataclass(frozen=True)
class HighLowPredictionSyncResult:
    """Database write manifest produced by minute high/low prediction sync.

    High/low prediction sync must consume EpochSyncResult and must not
    independently resolve station/version identity.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    rows_planned: int
    rows_deleted: int
    rows_written: int
    rows_skipped: int
    write_high_low_predictions: bool
    notes: list[str]


@dataclass(frozen=True)
class HfResolutionTarget:
    resolution_id: int
    temporal_resolution_code: str
    latest_time: pd.Timestamp


@dataclass(frozen=True)
class HfTidePredictionPlan:
    epoch_name: str
    epoch_id: int
    time_series_id: int
    resolution_id: int | None
    temporal_resolution_code: str | None
    interval: pd.Timedelta | None
    delete_start: pd.Timestamp
    delete_end: pd.Timestamp
    insert_start: pd.Timestamp | None
    insert_end: pd.Timestamp | None
    rows_planned: int
    hourly_frame: pd.DataFrame


@dataclass(frozen=True)
class PredictionDbWindow:
    """DB metadata used to bound prediction writes for one processed record.

    FD/current best_available records use the generated operational windows and
    therefore have no date_begin/date_end bound here.

    RQ records use the rq/hourly row from date_range_by_time_series_quality.
    """

    resolution_id: int
    temporal_resolution_code: str
    record_quality_short_name: str | None
    date_begin: pd.Timestamp | None
    date_end: pd.Timestamp | None
    date_range_last_update: pd.Timestamp | None


@dataclass(frozen=True)
class StaleRecentEpochCleanupResult:
    """Cleanup manifest for stale rolling RECENT_* epochs.

    Cleanup is scoped to one processed DB target: time_series_id + input_basis_id.
    It deletes only RECENT_* epochs that are no longer selected by the current
    run, plus their child datum/constituent/prediction rows.
    """

    record_id: str
    station_kind: str
    time_series_id: int
    id_from_source: str
    input_basis_code: str
    input_basis_id: int
    current_epoch_names: list[str]
    stale_epochs: list[dict[str, Any]]
    datum_rows_deleted: int
    constituent_rows_deleted: int
    tide_prediction_rows_deleted: int
    hf_tide_prediction_rows_deleted: int
    high_low_prediction_rows_deleted: int
    epoch_rows_deleted: int
    write_cleanup: bool


@dataclass(frozen=True)
class PredictionAutoCleanupResult:
    """Cleanup manifest for superseded best_available prediction rows.

    This cleanup is DB-driven by public.date_range_by_time_series_quality.
    It trims superseded FD/best_available prediction rows to their valid
    materialized-view date range and does not independently resolve station
    identity.
    """

    current_time_series_id: int
    current_id_from_source: str
    fd_quality_short_name: str
    temporal_resolution_code: str
    actions: list[dict[str, Any]]
    tide_prediction_rows_deleted: int
    hf_tide_prediction_rows_deleted: int
    high_low_prediction_rows_deleted: int
    write_cleanup: bool
    notes: list[str]


@dataclass(frozen=True)
class StationSourceReconciliation:
    station_id: str

    # Identity rows in public.time_series. These are valid DB identities/targets,
    # but they do NOT prove RQ observations exist.
    db_time_series_versions: list[str]

    # DB-authoritative RQ/hourly availability from
    # public.date_range_by_time_series_quality.
    db_rq_versions: list[str]

    # Metadata-listed RQ versions from UHSLC/ERDDAP metadata.
    erddap_rq_versions: list[str]

    # Versions safe to process as RQ: present in DB rq/hourly ranges and metadata.
    rq_versions_to_process: list[str]

    # DB says rq/hourly exists, but metadata does not list the version.
    db_only_rq_versions: list[str]

    # Metadata lists the version, but DB has no rq/hourly date range for it.
    # These are ignored for RQ processing because DB is the decider.
    erddap_metadata_only_rq_versions: list[str]

    # public.time_series has an identity row, but DB has no rq/hourly range.
    db_time_series_without_rq_versions: list[str]

    # Metadata lists an RQ version, but no matching time_series identity exists.
    erddap_versions_without_time_series: list[str]

    fd_source_record_id: str
    fd_time_series_id: int | None
    fd_id_from_source: str | None
    status: str


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _input_basis_code_for_record(station_kind: str) -> str:
    if station_kind.upper() == "FD":
        return DATABASE_POLICY.fd_input_basis_code
    return DATABASE_POLICY.rq_input_basis_code


def _resolve_database_process_env() -> str:
    """Resolve the environment used to locate external Timescale utilities."""

    process_env = (
        os.getenv("PROCESS_ENV")
        or DATABASE_POLICY.process_env_default
    ).strip().lower()

    process_home_by_env = dict(DATABASE_POLICY.timescale_process_home_by_env)

    if process_env not in process_home_by_env:
        raise RuntimeError(
            f"Invalid PROCESS_ENV={process_env!r}; expected one of "
            f"{sorted(process_home_by_env)}."
        )

    return process_env


def _candidate_database_utils_dirs() -> list[Path]:
    """Return candidate directories containing env_utils.py/timescale_utils.py."""

    candidates: list[Path] = []

    # 1. Highest priority: explicit config override.
    if DATABASE_POLICY.timescale_utils_dir:
        candidates.append(Path(DATABASE_POLICY.timescale_utils_dir))

    # 2. Runtime override without changing code.
    env_utils_dir = os.getenv("TIMESCALE_UTILS_DIR")
    if env_utils_dir:
        candidates.append(Path(env_utils_dir))

    # 3. Standard process-home convention, if exported by runtime shell.
    process_home = os.getenv("PROCESS_HOME")
    if process_home:
        candidates.append(Path(process_home) / "utils")

    # 4. Fallback mapping from PROCESS_ENV.
    process_env = _resolve_database_process_env()
    process_home_by_env = dict(DATABASE_POLICY.timescale_process_home_by_env)
    candidates.append(Path(process_home_by_env[process_env]) / "utils")

    # Preserve order while removing duplicates.
    unique_candidates: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        resolved_str = str(resolved)

        if resolved_str in seen:
            continue

        seen.add(resolved_str)
        unique_candidates.append(resolved)

    return unique_candidates


@lru_cache(maxsize=1)
def _load_database_tools():
    """Dynamically import Timescale utilities only when database access is needed."""

    attempted_dirs = []

    for candidate in _candidate_database_utils_dirs():
        attempted_dirs.append(str(candidate))

        env_utils_file = candidate / "env_utils.py"
        timescale_utils_file = candidate / "timescale_utils.py"

        if not env_utils_file.is_file() or not timescale_utils_file.is_file():
            continue

        candidate_str = str(candidate)

        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

        try:
            from env_utils import CommonUtils
            from timescale_utils import TSDataProcessor

        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                f"Found database utility directory at {candidate_str}, "
                "but importing env_utils/timescale_utils failed. This may mean "
                "a dependency of those modules is missing from the active Python "
                f"environment. Original error: {exc}"
            ) from exc

        log(f"Loaded database utilities from {candidate_str}")
        return CommonUtils, TSDataProcessor

    raise ModuleNotFoundError(
        "Could not locate env_utils.py and timescale_utils.py for database writes. "
        "Set one of the following: "
        "DATABASE_POLICY.timescale_utils_dir, TIMESCALE_UTILS_DIR, PROCESS_HOME, "
        "or PROCESS_ENV. "
        f"Attempted directories: {attempted_dirs}"
    )


@contextmanager
def _tsdb_connection():
    """Open a Timescale/Postgres connection and always close it.

    Write functions should still explicitly commit/rollback around the work they
    perform. This helper only centralizes connection creation/cleanup.
    """

    CommonUtils, TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    ts_processor = TSDataProcessor()
    conn = env_utils.connect_2_tsdb()

    try:
        yield conn, ts_processor
    finally:
        conn.close()


def _validate_database_write_policy_or_die() -> None:
    """Reject DB write combinations that can create partial operational state."""

    core_write_flags = {
        "write_epochs": bool(DATABASE_POLICY.write_epochs),
        "write_datums": bool(DATABASE_POLICY.write_datums),
        "write_constituents": bool(DATABASE_POLICY.write_constituents),
    }

    if any(core_write_flags.values()) and not all(core_write_flags.values()):
        raise RuntimeError(
            "Invalid DATABASE_POLICY core write combination. Operational core "
            "table writes are atomic now, so write_epochs, write_datums, and "
            "write_constituents must be all True or all False. "
            f"Current values: {core_write_flags}."
        )

    dependent_write_flags = {
        "write_tide_predictions": bool(DATABASE_POLICY.write_tide_predictions),
        "write_hf_tide_predictions": bool(DATABASE_POLICY.write_hf_tide_predictions),
        "write_high_low_predictions": bool(DATABASE_POLICY.write_high_low_predictions),
        "write_prediction_auto_cleanup": bool(DATABASE_POLICY.write_prediction_auto_cleanup),
        "write_prediction_cutover_cleanup": bool(DATABASE_POLICY.write_prediction_cutover_cleanup),
        "write_stale_recent_epoch_cleanup": bool(
            DATABASE_POLICY.write_stale_recent_epoch_cleanup
        ),
    }

    if any(dependent_write_flags.values()) and not all(core_write_flags.values()):
        raise RuntimeError(
            "Invalid DATABASE_POLICY dependent write combination. Prediction "
            "writes/cleanups and stale RECENT epoch cleanup require atomic core "
            "writes first, so write_epochs, write_datums, and "
            "write_constituents must all be True. "
            f"Core values: {core_write_flags}; dependent values: "
            f"{dependent_write_flags}."
        )

    if (
        DATABASE_POLICY.write_hf_tide_predictions
        and not DATABASE_POLICY.write_tide_predictions
    ):
        raise RuntimeError(
            "Invalid DATABASE_POLICY prediction write combination. "
            "write_hf_tide_predictions=True requires "
            "write_tide_predictions=True so the derived HF product cannot be "
            "committed without its authoritative hourly source product."
        )


def _time_series_id_from_source_prefix(station_id: str) -> str:
    """Return the zero-padded DB id_from_source prefix for a UHSLC station."""

    return str(int(station_id)).zfill(6)


def _rq_id_from_source(station_id: str, version: str) -> str:
    """Return the exact DB id_from_source expected for an RQ station version."""

    return f"{_time_series_id_from_source_prefix(station_id)}{str(version).upper()}"


def _rq_record_parts(record_id: str) -> tuple[str, str] | None:
    """Parse a lettered record id such as 014a into (station_id, version)."""

    match = re.fullmatch(r"\s*0*(\d{1,6})([A-Za-z])\s*", str(record_id))

    if match is None:
        return None

    return str(int(match.group(1))).zfill(3), match.group(2).upper()


def _version_from_id_from_source(station_id: str, id_from_source: str) -> str | None:
    """Return the RQ suffix in an id_from_source row for this station, if any."""

    prefix = _time_series_id_from_source_prefix(station_id)
    value = str(id_from_source).strip().upper()

    if not value.startswith(prefix):
        return None

    suffix = value[len(prefix):]

    if re.fullmatch(r"[A-Z]", suffix):
        return suffix

    return None


def _fetchone_dict(conn, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()

        if row is None:
            return None

        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))


def _fetchall_dicts(conn, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]


def _query_time_series_row_by_id(conn, time_series_id: int) -> DBTimeSeriesRow | None:
    row = _fetchone_dict(
        conn,
        """
        SELECT id, id_from_source
        FROM public.time_series
        WHERE id = %s
        """,
        (int(time_series_id),),
    )

    if row is None:
        return None

    return DBTimeSeriesRow(
        id=int(row["id"]),
        id_from_source=str(row["id_from_source"]),
    )


def _query_time_series_rows_by_id_from_source(
    conn,
    id_from_source: str,
) -> list[DBTimeSeriesRow]:
    rows = _fetchall_dicts(
        conn,
        """
        SELECT id, id_from_source
        FROM public.time_series
        WHERE upper(id_from_source) = upper(%s)
        ORDER BY id
        """,
        (str(id_from_source),),
    )

    return [
        DBTimeSeriesRow(
            id=int(row["id"]),
            id_from_source=str(row["id_from_source"]),
        )
        for row in rows
    ]


def _query_exact_time_series_row_by_id_from_source(
    conn,
    id_from_source: str,
) -> DBTimeSeriesRow:
    rows = _query_time_series_rows_by_id_from_source(conn, id_from_source)

    if not rows:
        raise RuntimeError(
            f"No time_series row found for exact id_from_source={id_from_source!r}."
        )

    if len(rows) > 1:
        raise RuntimeError(
            f"Multiple time_series rows found for exact id_from_source={id_from_source!r}: "
            f"{rows}. Refusing to choose one."
        )

    return rows[0]


def _query_station_quality_versions_from_date_ranges(
    conn,
    station_id: str,
    *,
    record_quality_short_name: str,
    temporal_resolution_code: str,
) -> list[str]:
    """Return station versions with authoritative quality/resolution date ranges.

    For RQ availability, call this with rq/hourly. This intentionally uses
    public.date_range_by_time_series_quality as the availability source instead
    of public.time_series alone. When a time_series row is linked, prefer its
    id_from_source for version parsing because exact RQ writes use that identity.
    """

    prefix = _time_series_id_from_source_prefix(station_id)

    rows = _fetchall_dicts(
        conn,
        """
        SELECT DISTINCT
          COALESCE(ts.id_from_source, r.id_from_source) AS id_from_source
        FROM public.date_range_by_time_series_quality r
        LEFT JOIN public.time_series ts
          ON ts.id = r.time_series_id
        JOIN public.record_quality q
          ON q.id = r.quality_id
        JOIN public.temporal_resolution tr
          ON tr.id = r.resolution_id
        WHERE upper(COALESCE(ts.id_from_source, r.id_from_source)) LIKE upper(%s)
          AND lower(q.short_name) = lower(%s)
          AND tr.resolution = %s
        ORDER BY id_from_source
        """,
        (
            f"{prefix}%",
            str(record_quality_short_name),
            str(temporal_resolution_code),
        ),
    )

    versions: set[str] = set()

    for row in rows:
        version = _version_from_id_from_source(
            station_id,
            str(row["id_from_source"]),
        )
        if version is not None:
            versions.add(version)

    return sorted(versions)


def _query_station_time_series_rows(conn, station_id: str) -> list[DBTimeSeriesRow]:
    """Return candidate time_series rows for a station from id_from_source."""

    prefix = _time_series_id_from_source_prefix(station_id)
    rows = _fetchall_dicts(
        conn,
        """
        SELECT id, id_from_source
        FROM public.time_series
        WHERE upper(id_from_source) LIKE upper(%s)
        ORDER BY id_from_source
        """,
        (f"{prefix}%",),
    )

    out: list[DBTimeSeriesRow] = []

    for row in rows:
        id_from_source = str(row["id_from_source"])
        suffix = id_from_source.strip().upper()[len(prefix):]

        # Keep only the exact base row or single-letter station-version rows.
        # This avoids accidentally treating neighboring identifiers with the
        # same prefix as part of this station manifest.
        if suffix == "" or re.fullmatch(r"[A-Z]", suffix):
            out.append(
                DBTimeSeriesRow(
                    id=int(row["id"]),
                    id_from_source=id_from_source,
                )
            )

    return out


def _resolve_epoch_write_target(
    *,
    conn,
    ts_processor,
    record: dict[str, Any],
    input_basis_id: int,
) -> EpochWriteTarget:
    """Resolve and validate the DB target used for epoch writes.

    Research-quality records are version-specific and must match an exact
    id_from_source such as 000014A.

    Best-available/FD records are stream-specific: the unversioned ERDDAP
    source record, such as 014, is resolved by the DB utility layer to the DB
    row currently assigned to that published source stream.
    """

    record_id = str(record["record_id"])
    station_kind = str(record["station_kind"]).upper()
    input_basis_code = _input_basis_code_for_record(station_kind)

    if station_kind == "RQ":
        record_parts = _rq_record_parts(record_id)

        if record_parts is None:
            raise RuntimeError(f"Could not parse RQ record_id={record_id!r}.")

        station_id, version = record_parts
        expected_id_from_source = _rq_id_from_source(station_id, version)

        try:
            row = _query_exact_time_series_row_by_id_from_source(
                conn,
                expected_id_from_source,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"Refusing to write RQ epochs for {record_id}: {exc}"
            ) from exc

        return EpochWriteTarget(
            source_record_id=record_id,
            input_basis_code=input_basis_code,
            input_basis_id=int(input_basis_id),
            time_series_id=row.id,
            id_from_source=row.id_from_source,
            resolution_rule="rq_exact_id_from_source",
        )

    time_series_id = ts_processor.query_time_series_id_by_record_id_tsdb(
        conn,
        record_id,
    )
    row = _query_time_series_row_by_id(conn, int(time_series_id))

    if row is None:
        raise RuntimeError(
            f"Refusing to write FD epochs for {record_id}: DB resolver returned "
            f"time_series_id={time_series_id}, but that row was not found."
        )

    return EpochWriteTarget(
        source_record_id=record_id,
        input_basis_code=input_basis_code,
        input_basis_id=int(input_basis_id),
        time_series_id=row.id,
        id_from_source=row.id_from_source,
        resolution_rule="fd_unversioned_source_resolved_by_database",
    )


def _build_station_source_reconciliation(
    *,
    station_id: str,
    erddap_rq_versions: list[str],
) -> StationSourceReconciliation | None:
    if not DATABASE_POLICY.reconcile_station_inventory:
        return None

    CommonUtils, TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    ts_processor = TSDataProcessor()
    conn = env_utils.connect_2_tsdb()

    try:
        rows = _query_station_time_series_rows(conn, station_id)

        db_time_series_versions = sorted(
            {
                version
                for row in rows
                for version in [
                    _version_from_id_from_source(station_id, row.id_from_source)
                ]
                if version is not None
            }
        )

        db_rq_versions = _query_station_quality_versions_from_date_ranges(
            conn,
            station_id,
            record_quality_short_name=DATABASE_POLICY.rq_record_quality_short_name,
            temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
        )

        erddap_versions = sorted(str(version).upper() for version in erddap_rq_versions)

        db_rq_set = set(db_rq_versions)
        db_time_series_set = set(db_time_series_versions)
        erddap_set = set(erddap_versions)

        db_only = sorted(db_rq_set - erddap_set)
        erddap_metadata_only = sorted(erddap_set - db_rq_set)
        to_process = sorted(db_rq_set.intersection(erddap_set))

        db_time_series_without_rq = sorted(db_time_series_set - db_rq_set)
        erddap_without_time_series = sorted(erddap_set - db_time_series_set)

        fd_time_series_id: int | None = None
        fd_id_from_source: str | None = None

        try:
            fd_time_series_id = int(
                ts_processor.query_time_series_id_by_record_id_tsdb(
                    conn,
                    station_id,
                )
            )
            fd_row = _query_time_series_row_by_id(conn, fd_time_series_id)
            fd_id_from_source = None if fd_row is None else fd_row.id_from_source
        except Exception as exc:
            log(
                f"{station_id}: FD source reconciliation could not resolve DB target: "
                f"{exc}"
            )

        if fd_time_series_id is None:
            status = "source_gaps"
        elif db_only:
            # DB says rq/hourly should exist, but ERDDAP metadata does not list it.
            # In warn mode, process the safe intersection and report the gap.
            status = "source_gaps"
        elif not db_rq_versions:
            # No DB-authoritative rq/hourly date ranges. This is a valid FD-only
            # station state, even if metadata lists candidate RQ versions.
            status = "fd_only"
        elif erddap_metadata_only:
            # DB rq/hourly ranges are the decider. Extra metadata versions are
            # ignored for RQ processing but reported clearly.
            status = "ok_with_erddap_metadata_only"
        else:
            status = "ok"

        return StationSourceReconciliation(
            station_id=station_id,
            db_time_series_versions=db_time_series_versions,
            db_rq_versions=db_rq_versions,
            erddap_rq_versions=erddap_versions,
            rq_versions_to_process=to_process,
            db_only_rq_versions=db_only,
            erddap_metadata_only_rq_versions=erddap_metadata_only,
            db_time_series_without_rq_versions=db_time_series_without_rq,
            erddap_versions_without_time_series=erddap_without_time_series,
            fd_source_record_id=station_id,
            fd_time_series_id=fd_time_series_id,
            fd_id_from_source=fd_id_from_source,
            status=status,
        )

    finally:
        conn.close()


def _log_station_source_reconciliation(
    reconciliation: StationSourceReconciliation,
) -> None:
    log(
        f"Station {reconciliation.station_id}: source reconciliation | "
        f"DB time_series versions={reconciliation.db_time_series_versions or 'none'} | "
        f"DB rq/hourly versions={reconciliation.db_rq_versions or 'none'} | "
        f"ERDDAP metadata RQ versions={reconciliation.erddap_rq_versions or 'none'} | "
        f"to_process={reconciliation.rq_versions_to_process or 'none'} | "
        f"status={reconciliation.status}"
    )

    log(
        f"Station {reconciliation.station_id}: FD/best_available source "
        f"{reconciliation.fd_source_record_id} resolves to "
        f"time_series_id={reconciliation.fd_time_series_id}, "
        f"id_from_source={reconciliation.fd_id_from_source}"
    )

    if reconciliation.db_time_series_without_rq_versions:
        log(
            f"Station {reconciliation.station_id}: time_series identity versions "
            f"without DB rq/hourly availability: "
            f"{', '.join(reconciliation.db_time_series_without_rq_versions)}"
        )

    if reconciliation.db_only_rq_versions:
        log(
            f"Station {reconciliation.station_id}: DB rq/hourly versions missing from "
            f"ERDDAP metadata and skipped: "
            f"{', '.join(reconciliation.db_only_rq_versions)}"
        )

    if reconciliation.erddap_metadata_only_rq_versions:
        log(
            f"Station {reconciliation.station_id}: ERDDAP metadata RQ versions without "
            f"DB rq/hourly date ranges; ignored for RQ processing because DB is "
            f"authoritative: "
            f"{', '.join(reconciliation.erddap_metadata_only_rq_versions)}"
        )

    if reconciliation.erddap_versions_without_time_series:
        log(
            f"Station {reconciliation.station_id}: ERDDAP metadata RQ versions without "
            f"matching time_series identity rows: "
            f"{', '.join(reconciliation.erddap_versions_without_time_series)}"
        )

    if DATABASE_POLICY.reconciliation_mode == "strict" and reconciliation.status not in {
        "ok",
        "fd_only",
        "ok_with_erddap_metadata_only",
    }:
        raise RuntimeError(
            f"Station {reconciliation.station_id}: source reconciliation failed in "
            f"strict mode: {asdict(reconciliation)}"
        )


def _build_epoch_db_dataframe(
    *,
    record: dict[str, Any],
    time_series_id: int,
    input_basis_id: int,
    last_update: pd.Timestamp,
) -> pd.DataFrame:
    rows = []

    for idx, epoch_summary in enumerate(record["epochs"], start=1):
        ep = epoch_summary["epoch"]

        rows.append(
            {
                "date_begin": pd.Timestamp(ep["start"]),
                "date_end": pd.Timestamp(ep["end"]),
                "last_update": last_update,
                "primary": bool(epoch_summary["is_prediction_basis"]),
                "is_prediction_basis": bool(epoch_summary["is_prediction_basis"]),
                "time_series_id": int(time_series_id),
                "input_basis_id": int(input_basis_id),
                "name": ep["name"],
                "source": ep["source"],
                "role": ep["role"],
                "fit_begin": pd.Timestamp(epoch_summary["fit_begin"]),
                "fit_end": pd.Timestamp(epoch_summary["fit_end"]),
                "completion_fraction": ep.get("completion_fraction"),
                "n_expected": ep.get("n_expected"),
                "n_valid": ep.get("n_valid"),
                "tide_type": epoch_summary["datum"].get("tide_type"),
                "fit_rmse_mm": epoch_summary.get("epoch_rmse_mm"),
                "selection_rank": idx,
                "harmonic_mean_mm": epoch_summary.get("harmonic_mean_mm"),
                "harmonic_slope_mm_per_day": epoch_summary.get("harmonic_slope_mm_per_day"),
                "harmonic_constituent_count": epoch_summary.get("harmonic_constituent_count"),
            }
        )

    return pd.DataFrame(rows)


def _log_epoch_db_plan(record: dict[str, Any]) -> None:
    input_basis_code = _input_basis_code_for_record(record["station_kind"])

    log(
        f"{record['record_id']}: database epoch sync plan | "
        f"input_basis={input_basis_code} | "
        f"epochs={len(record['epochs'])} | "
        f"write_epochs={DATABASE_POLICY.write_epochs}"
    )

    for epoch_summary in record["epochs"]:
        ep = epoch_summary["epoch"]
        log(
            f"{record['record_id']}: "
            f"{'WOULD UPSERT' if not DATABASE_POLICY.write_epochs else 'UPSERT'} epoch "
            f"{ep['name']} | primary={epoch_summary['is_prediction_basis']} | "
            f"is_prediction_basis={epoch_summary['is_prediction_basis']} | "
            f"source={ep['source']} | role={ep['role']} | "
            f"date_window={ep['start']} to {ep['end']} | "
            f"fit_window={epoch_summary.get('fit_begin')} to {epoch_summary.get('fit_end')}"
        )


def _coerce_database_parameter(value: Any) -> Any:
    """Convert pandas/numpy scalar values into DB-driver-friendly parameters."""

    if _is_missing_database_value(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if isinstance(value, np.generic):
        return value.item()

    return value


def _validate_epoch_db_dataframe(epoch_df: pd.DataFrame) -> tuple[int, int]:
    required_columns = {
        "date_begin",
        "date_end",
        "last_update",
        "primary",
        "is_prediction_basis",
        "time_series_id",
        "input_basis_id",
        "name",
        "source",
        "role",
        "fit_begin",
        "fit_end",
        "completion_fraction",
        "n_expected",
        "n_valid",
        "tide_type",
        "fit_rmse_mm",
        "selection_rank",
        "harmonic_mean_mm",
        "harmonic_slope_mm_per_day",
        "harmonic_constituent_count",
    }

    if not required_columns.issubset(epoch_df.columns):
        missing = required_columns - set(epoch_df.columns)
        raise ValueError(f"ERROR: Epoch dataframe is missing required columns: {missing}")

    if epoch_df.empty:
        raise ValueError("ERROR: Epoch dataframe is empty; no rows to upsert.")

    non_null_columns = {
        "date_begin",
        "date_end",
        "last_update",
        "primary",
        "is_prediction_basis",
        "time_series_id",
        "input_basis_id",
        "name",
        "source",
        "role",
        "fit_begin",
        "fit_end",
        "selection_rank",
    }

    null_counts = epoch_df[list(non_null_columns)].isnull().sum()
    bad_null_counts = null_counts[null_counts > 0]

    if not bad_null_counts.empty:
        raise ValueError(
            "ERROR: Epoch dataframe contains null values in non-null columns: "
            f"{bad_null_counts.to_dict()}"
        )

    unique_time_series_ids = epoch_df["time_series_id"].dropna().unique()
    unique_input_basis_ids = epoch_df["input_basis_id"].dropna().unique()

    if len(unique_time_series_ids) != 1:
        raise ValueError("ERROR: Epoch dataframe must contain exactly one time_series_id.")

    if len(unique_input_basis_ids) != 1:
        raise ValueError("ERROR: Epoch dataframe must contain exactly one input_basis_id.")

    primary_count = int(epoch_df["primary"].fillna(False).astype(bool).sum())
    basis_count = int(epoch_df["is_prediction_basis"].fillna(False).astype(bool).sum())

    if primary_count != 1:
        raise ValueError(f"ERROR: Expected exactly one primary epoch row; found {primary_count}.")

    if basis_count != 1:
        raise ValueError(
            f"ERROR: Expected exactly one is_prediction_basis epoch row; found {basis_count}."
        )

    return int(unique_time_series_ids[0]), int(unique_input_basis_ids[0])


def _upsert_epochs_to_database(conn, epoch_df: pd.DataFrame) -> dict[str, int]:
    """Upsert epoch rows without committing. Caller owns commit/rollback."""

    time_series_id, input_basis_id = _validate_epoch_db_dataframe(epoch_df)

    print(
        f"LOG: Prepared {len(epoch_df)} epoch row(s) for time_series_id={time_series_id}, "
        f"input_basis_id={input_basis_id}. execute_writes=True."
    )

    insert_columns = [
        "date_begin",
        "date_end",
        "last_update",
        "primary",
        "is_prediction_basis",
        "time_series_id",
        "input_basis_id",
        "name",
        "source",
        "role",
        "fit_begin",
        "fit_end",
        "completion_fraction",
        "n_expected",
        "n_valid",
        "tide_type",
        "fit_rmse_mm",
        "selection_rank",
        "harmonic_mean_mm",
        "harmonic_slope_mm_per_day",
        "harmonic_constituent_count",
    ]

    rows = [
        tuple(_coerce_database_parameter(value) for value in row)
        for row in epoch_df.loc[:, insert_columns].itertuples(index=False, name=None)
    ]

    insert_sql = """
        INSERT INTO public.epoch (
            date_begin,
            date_end,
            last_update,
            "primary",
            is_prediction_basis,
            time_series_id,
            input_basis_id,
            name,
            "source",
            "role",
            fit_begin,
            fit_end,
            completion_fraction,
            n_expected,
            n_valid,
            tide_type,
            fit_rmse_mm,
            selection_rank,
            harmonic_mean_mm,
            harmonic_slope_mm_per_day,
            harmonic_constituent_count
        )
        VALUES %s
        ON CONFLICT (time_series_id, input_basis_id, name)
        DO UPDATE SET
            date_begin = EXCLUDED.date_begin,
            date_end = EXCLUDED.date_end,
            last_update = EXCLUDED.last_update,
            "primary" = EXCLUDED."primary",
            is_prediction_basis = EXCLUDED.is_prediction_basis,
            "source" = EXCLUDED."source",
            "role" = EXCLUDED."role",
            fit_begin = EXCLUDED.fit_begin,
            fit_end = EXCLUDED.fit_end,
            completion_fraction = EXCLUDED.completion_fraction,
            n_expected = EXCLUDED.n_expected,
            n_valid = EXCLUDED.n_valid,
            tide_type = EXCLUDED.tide_type,
            fit_rmse_mm = EXCLUDED.fit_rmse_mm,
            selection_rank = EXCLUDED.selection_rank,
            harmonic_mean_mm = EXCLUDED.harmonic_mean_mm,
            harmonic_slope_mm_per_day = EXCLUDED.harmonic_slope_mm_per_day,
            harmonic_constituent_count = EXCLUDED.harmonic_constituent_count
        RETURNING id, name
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.epoch
            SET
                "primary" = false,
                is_prediction_basis = false
            WHERE time_series_id = %s
              AND input_basis_id = %s
              AND ("primary" IS TRUE OR is_prediction_basis IS TRUE)
            """,
            (time_series_id, input_basis_id),
        )

        returned_rows = execute_values(
            cur,
            insert_sql,
            rows,
            fetch=True,
        )

    epoch_id_by_name = {str(name): int(epoch_id) for epoch_id, name in returned_rows}

    print(
        f"LOG: Successfully upserted {len(epoch_id_by_name)} epoch row(s) "
        f"for time_series_id={time_series_id}, input_basis_id={input_basis_id}."
    )

    return epoch_id_by_name


def _sync_record_epochs_to_database(
    record: dict[str, Any],
    *,
    last_update: pd.Timestamp,
) -> EpochSyncResult | None:
    """Resolve, log, and optionally write epoch rows for a processed record.

    This function is the one authoritative place where a processed ERDDAP
    record is resolved to a database time_series/input_basis target.

    Future dependent-table writers should use the returned EpochSyncResult
    rather than re-resolving station/version identity.
    """

    if not DATABASE_POLICY.log_epoch_plan and not DATABASE_POLICY.write_epochs:
        return None

    CommonUtils, TSDataProcessor = _load_database_tools()

    env_utils = CommonUtils()
    ts_processor = TSDataProcessor()

    conn = env_utils.connect_2_tsdb()

    try:
        input_basis_code = _input_basis_code_for_record(record["station_kind"])
        input_basis_id = ts_processor.query_prediction_input_basis_id_tsdb(
            conn,
            input_basis_code,
        )

        write_target = _resolve_epoch_write_target(
            conn=conn,
            ts_processor=ts_processor,
            record=record,
            input_basis_id=input_basis_id,
        )

        log(
            f"{record['record_id']}: resolved epoch DB target | "
            f"source_record_id={write_target.source_record_id} | "
            f"input_basis={write_target.input_basis_code} | "
            f"time_series_id={write_target.time_series_id} | "
            f"id_from_source={write_target.id_from_source} | "
            f"resolution_rule={write_target.resolution_rule}"
        )

        epoch_df = _build_epoch_db_dataframe(
            record=record,
            time_series_id=write_target.time_series_id,
            input_basis_id=input_basis_id,
            last_update=last_update,
        )

        _log_epoch_db_plan(record)

        epoch_id_by_name: dict[str, int] = {}

        if DATABASE_POLICY.write_epochs:
            raw_epoch_id_by_name = ts_processor.upsert_tidal_epochs_to_tsdb(
                conn,
                epoch_df,
                execute_writes=True,
            )

            epoch_id_by_name = {
                str(epoch_name): int(epoch_id)
                for epoch_name, epoch_id in raw_epoch_id_by_name.items()
            }

            expected_epoch_names = {
                str(epoch_summary["epoch"]["name"])
                for epoch_summary in record["epochs"]
            }
            returned_epoch_names = set(epoch_id_by_name)

            if returned_epoch_names != expected_epoch_names:
                raise RuntimeError(
                    f"{record['record_id']}: epoch upsert returned unexpected "
                    f"epoch names. expected={sorted(expected_epoch_names)}, "
                    f"returned={sorted(returned_epoch_names)}"
                )

            log(
                f"{record['record_id']}: database epoch sync complete | "
                f"epoch_ids={epoch_id_by_name}"
            )

        return EpochSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            source_record_id=write_target.source_record_id,
            input_basis_code=write_target.input_basis_code,
            input_basis_id=int(write_target.input_basis_id),
            time_series_id=int(write_target.time_series_id),
            id_from_source=write_target.id_from_source,
            resolution_rule=write_target.resolution_rule,
            epoch_id_by_name=epoch_id_by_name,
            write_epochs=bool(DATABASE_POLICY.write_epochs),
        )

    finally:
        conn.close()


DATUM_VALUE_KEY_TO_DEFINITION_SHORT_NAME = {
    "dhq": "dhq",
    "dlq": "dlq",
    "dtl": "dtl",
    "gt": "gt",
    "hat": "hat",
    "lat": "lat",
    "mhhw": "mhhw",
    "mhw": "mhw",
    "mllw": "mllw",
    "mlw": "mlw",
    "mn": "mn",
    "msl": "msl",
    "mtl": "mtl",
    "stnd": "stnd",
}

DATUM_TIME_KEY_TO_DEFINITION_SHORT_NAME = {
    "hat_time": "hat_time",
    "lat_time": "lat_time",
}

# Computed datum values in this pipeline are millimeters relative to station
# zero. public.datum.value stores elevations/ranges in meters.
DATUM_VALUE_MM_TO_DATABASE_METERS = 0.001
DATUM_DATABASE_VALUE_DECIMAL_PLACES = 4


def _is_missing_database_value(value: Any) -> bool:
    if value is None:
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _query_datum_definition_id_by_short_name(conn) -> dict[str, int]:
    rows = _fetchall_dicts(
        conn,
        """
        SELECT id, short_name
        FROM public.datum_definition
        """,
        (),
    )

    return {
        str(row["short_name"]).strip().lower(): int(row["id"])
        for row in rows
    }


def _build_datum_db_dataframe(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    definition_id_by_short_name: dict[str, int],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    if not epoch_sync.epoch_id_by_name:
        raise RuntimeError(
            f"{record['record_id']}: cannot build datum DB rows because "
            "epoch_sync.epoch_id_by_name is empty. Enable write_epochs=True "
            "before write_datums=True."
        )

    for epoch_summary in record["epochs"]:
        epoch_name = str(epoch_summary["epoch"]["name"])

        if epoch_name not in epoch_sync.epoch_id_by_name:
            raise RuntimeError(
                f"{record['record_id']}: epoch {epoch_name!r} is missing from "
                f"epoch_sync.epoch_id_by_name={epoch_sync.epoch_id_by_name}."
            )

        epoch_id = int(epoch_sync.epoch_id_by_name[epoch_name])
        datum_values = epoch_summary["datum"]

        for datum_key, raw_value in datum_values.items():
            short_name = DATUM_VALUE_KEY_TO_DEFINITION_SHORT_NAME.get(
                str(datum_key).strip().lower()
            )

            if short_name is None:
                continue

            if short_name not in definition_id_by_short_name:
                raise RuntimeError(
                    f"{record['record_id']}: datum_definition short_name={short_name!r} "
                    "is missing from public.datum_definition."
                )

            if _is_missing_database_value(raw_value):
                continue

            rows.append(
                {
                    "epoch_name": epoch_name,
                    "definition_short_name": short_name,
                    "definition_id": int(definition_id_by_short_name[short_name]),
                    "epoch_id": epoch_id,
                    "time_series_id": int(epoch_sync.time_series_id),
                    "value": round(
                        float(raw_value) * DATUM_VALUE_MM_TO_DATABASE_METERS,
                        DATUM_DATABASE_VALUE_DECIMAL_PLACES,
                    ),
                    "time": None,
                }
            )

        for datum_key, raw_time in datum_values.items():
            short_name = DATUM_TIME_KEY_TO_DEFINITION_SHORT_NAME.get(
                str(datum_key).strip().lower()
            )

            if short_name is None:
                continue

            if short_name not in definition_id_by_short_name:
                raise RuntimeError(
                    f"{record['record_id']}: datum_definition short_name={short_name!r} "
                    "is missing from public.datum_definition."
                )

            if _is_missing_database_value(raw_time):
                continue

            rows.append(
                {
                    "epoch_name": epoch_name,
                    "definition_short_name": short_name,
                    "definition_id": int(definition_id_by_short_name[short_name]),
                    "epoch_id": epoch_id,
                    "time_series_id": int(epoch_sync.time_series_id),
                    "value": None,
                    "time": pd.Timestamp(raw_time),
                }
            )

    return pd.DataFrame(rows)


def _log_datum_db_plan(record: dict[str, Any], datum_df: pd.DataFrame) -> None:
    log(
        f"{record['record_id']}: database datum sync plan | "
        f"datums={len(datum_df)} | value_units=meters | "
        f"write_datums={DATABASE_POLICY.write_datums}"
    )

    if datum_df.empty:
        return

    for epoch_name, epoch_df in datum_df.groupby("epoch_name", sort=True):
        short_names = ", ".join(sorted(epoch_df["definition_short_name"].astype(str)))
        log(
            f"{record['record_id']}: "
            f"{'UPSERT' if DATABASE_POLICY.write_datums else 'WOULD UPSERT'} "
            f"{len(epoch_df)} datum row(s) for epoch {epoch_name}: {short_names}"
        )


def _upsert_datums_to_database(
    conn,
    datum_df: pd.DataFrame,
    *,
    manage_transaction: bool = True,
) -> dict[str, dict[str, int]]:
    """Upsert datum rows. By default owns its transaction for legacy callers."""
    datum_id_by_epoch_and_short_name: dict[str, dict[str, int]] = {}

    try:
        with conn.cursor() as cur:
            for row in datum_df.to_dict("records"):
                raw_value = row["value"]
                raw_time = row["time"]

                value = (
                    None
                    if _is_missing_database_value(raw_value)
                    else float(raw_value)
                )
                datum_time = (
                    None
                    if _is_missing_database_value(raw_time)
                    else pd.Timestamp(raw_time).to_pydatetime()
                )

                cur.execute(
                    """
                    INSERT INTO public.datum (
                        value,
                        definition_id,
                        epoch_id,
                        "time",
                        time_series_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (time_series_id, epoch_id, definition_id)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        "time" = EXCLUDED."time"
                    RETURNING id
                    """,
                    (
                        value,
                        int(row["definition_id"]),
                        int(row["epoch_id"]),
                        datum_time,
                        int(row["time_series_id"]),
                    ),
                )

                datum_id = int(cur.fetchone()[0])
                epoch_name = str(row["epoch_name"])
                short_name = str(row["definition_short_name"])

                datum_id_by_epoch_and_short_name.setdefault(epoch_name, {})[
                    short_name
                ] = datum_id

        if manage_transaction:
            conn.commit()

    except Exception:
        if manage_transaction:
            conn.rollback()
        raise

    return datum_id_by_epoch_and_short_name


def _sync_record_datums_to_database(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
) -> DatumSyncResult | None:
    """Resolve, log, and optionally write datum rows for a processed record.

    This function intentionally consumes EpochSyncResult. It must not perform
    independent station/version resolution.
    """

    if not DATABASE_POLICY.log_datum_plan and not DATABASE_POLICY.write_datums:
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: database datum sync skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        if DATABASE_POLICY.write_datums:
            raise RuntimeError(
                f"{record['record_id']}: write_datums=True requires populated "
                "epoch_sync.epoch_id_by_name. Enable write_epochs=True first."
            )

        log(
            f"{record['record_id']}: database datum sync skipped | "
            "no epoch IDs available in dry-run/no-write mode"
        )

        return DatumSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            datum_id_by_epoch_and_short_name={},
            rows_planned=0,
            rows_written=0,
            write_datums=bool(DATABASE_POLICY.write_datums),
        )

    CommonUtils, _TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    conn = env_utils.connect_2_tsdb()

    try:
        definition_id_by_short_name = _query_datum_definition_id_by_short_name(conn)

        datum_df = _build_datum_db_dataframe(
            record=record,
            epoch_sync=epoch_sync,
            definition_id_by_short_name=definition_id_by_short_name,
        )

        _log_datum_db_plan(record, datum_df)

        datum_id_by_epoch_and_short_name: dict[str, dict[str, int]] = {}

        if DATABASE_POLICY.write_datums:
            datum_id_by_epoch_and_short_name = _upsert_datums_to_database(
                conn,
                datum_df,
            )

            written_count = sum(
                len(short_name_ids)
                for short_name_ids in datum_id_by_epoch_and_short_name.values()
            )

            if written_count != len(datum_df):
                raise RuntimeError(
                    f"{record['record_id']}: datum upsert returned {written_count} "
                    f"row id(s), but planned {len(datum_df)} datum row(s)."
                )

            log(
                f"{record['record_id']}: database datum sync complete | "
                f"datum_ids={datum_id_by_epoch_and_short_name}"
            )

        return DatumSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            datum_id_by_epoch_and_short_name=datum_id_by_epoch_and_short_name,
            rows_planned=int(len(datum_df)),
            rows_written=sum(
                len(short_name_ids)
                for short_name_ids in datum_id_by_epoch_and_short_name.values()
            ),
            write_datums=bool(DATABASE_POLICY.write_datums),
        )

    finally:
        conn.close()


CONSTITUENT_AMPLITUDE_DECIMAL_PLACES = 4
CONSTITUENT_PHASE_DECIMAL_PLACES = 4


def _normalize_constituent_short_name(value: Any) -> str:
    return str(value).strip().upper()


def _normalize_phase_deg(value: Any) -> float:
    phase = float(value) % 360.0
    return round(phase, CONSTITUENT_PHASE_DECIMAL_PLACES)


def _query_constituent_definition_id_by_short_name(conn) -> dict[str, int]:
    rows = _fetchall_dicts(
        conn,
        """
        SELECT id, short_name
        FROM public.constituent_definition
        """,
        (),
    )

    return {
        _normalize_constituent_short_name(row["short_name"]): int(row["id"])
        for row in rows
    }


def _build_constituent_db_plan_dataframe(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    if not epoch_sync.epoch_id_by_name:
        raise RuntimeError(
            f"{record['record_id']}: cannot build constituent DB rows because "
            "epoch_sync.epoch_id_by_name is empty. Enable write_epochs=True "
            "before write_constituents=True."
        )

    for epoch_summary in record["epochs"]:
        epoch_name = str(epoch_summary["epoch"]["name"])

        if epoch_name not in epoch_sync.epoch_id_by_name:
            raise RuntimeError(
                f"{record['record_id']}: epoch {epoch_name!r} is missing from "
                f"epoch_sync.epoch_id_by_name={epoch_sync.epoch_id_by_name}."
            )

        epoch_id = int(epoch_sync.epoch_id_by_name[epoch_name])
        harmonic_path = epoch_summary["harmonic_artifact"]["pickle"]
        harmonics = load_harmonic_result(harmonic_path)

        names = list(harmonics.constituent)
        amplitudes = list(harmonics.amplitude_mm)
        phases = list(harmonics.phase_deg)

        if not (len(names) == len(amplitudes) == len(phases)):
            raise RuntimeError(
                f"{record['record_id']} {epoch_name}: harmonic constituent arrays "
                f"have mismatched lengths: names={len(names)}, "
                f"amplitudes={len(amplitudes)}, phases={len(phases)}."
            )

        for constituent_order, (name, amplitude_raw, phase_raw) in enumerate(
            zip(names, amplitudes, phases),
            start=1,
        ):
            short_name = _normalize_constituent_short_name(name)

            if not short_name:
                raise RuntimeError(
                    f"{record['record_id']} {epoch_name}: empty constituent name "
                    f"at order {constituent_order}."
                )

            if _is_missing_database_value(amplitude_raw):
                raise RuntimeError(
                    f"{record['record_id']} {epoch_name}: missing amplitude for "
                    f"constituent {short_name}."
                )

            if _is_missing_database_value(phase_raw):
                raise RuntimeError(
                    f"{record['record_id']} {epoch_name}: missing phase for "
                    f"constituent {short_name}."
                )

            amplitude_mm = round(
                float(amplitude_raw),
                CONSTITUENT_AMPLITUDE_DECIMAL_PLACES,
            )

            if amplitude_mm < 0:
                raise RuntimeError(
                    f"{record['record_id']} {epoch_name}: negative amplitude "
                    f"for constituent {short_name}: {amplitude_mm}."
                )

            rows.append(
                {
                    "epoch_name": epoch_name,
                    "definition_short_name": short_name,
                    "epoch_id": epoch_id,
                    "time_series_id": int(epoch_sync.time_series_id),
                    "amplitude_mm": amplitude_mm,
                    "phase_deg": _normalize_phase_deg(phase_raw),
                    "constituent_order": int(constituent_order),
                }
            )

    return pd.DataFrame(rows)


def _log_constituent_db_plan(
    record: dict[str, Any],
    constituent_df: pd.DataFrame,
    *,
    missing_definition_short_names: list[str],
) -> None:
    log(
        f"{record['record_id']}: database constituent sync plan | "
        f"constituents={len(constituent_df)} | "
        f"missing_definitions={len(missing_definition_short_names)} | "
        f"write_constituents={DATABASE_POLICY.write_constituents}"
    )

    if missing_definition_short_names:
        log(
            f"{record['record_id']}: "
            f"{'CREATE' if DATABASE_POLICY.write_constituents else 'WOULD CREATE'} "
            f"constituent_definition row(s): "
            f"{', '.join(missing_definition_short_names)}"
        )

    if constituent_df.empty:
        return

    for epoch_name, epoch_df in constituent_df.groupby("epoch_name", sort=True):
        log(
            f"{record['record_id']}: "
            f"{'REPLACE' if DATABASE_POLICY.write_constituents else 'WOULD REPLACE'} "
            f"{len(epoch_df)} constituent row(s) for epoch {epoch_name}"
        )


def _ensure_constituent_definitions(
    conn,
    constituent_df: pd.DataFrame,
) -> dict[str, int]:
    """Create missing constituent_definition rows and return all definition ids.

    This intentionally writes only minimal definition metadata. Detailed
    frequency/species/description metadata can be backfilled later.
    """

    if constituent_df.empty:
        return _query_constituent_definition_id_by_short_name(conn)

    display_order_by_short_name = (
        constituent_df.groupby("definition_short_name")["constituent_order"]
        .min()
        .sort_values()
        .to_dict()
    )

    with conn.cursor() as cur:
        for short_name, display_order in display_order_by_short_name.items():
            cur.execute(
                """
                INSERT INTO public.constituent_definition (
                    short_name,
                    display_name,
                    display_order
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (short_name)
                DO UPDATE SET
                    display_name = COALESCE(
                        public.constituent_definition.display_name,
                        EXCLUDED.display_name
                    ),
                    display_order = COALESCE(
                        public.constituent_definition.display_order,
                        EXCLUDED.display_order
                    )
                """,
                (
                    short_name,
                    short_name,
                    int(display_order),
                ),
            )

    return _query_constituent_definition_id_by_short_name(conn)


def _write_constituents_to_database(
    conn,
    constituent_df: pd.DataFrame,
    *,
    manage_transaction: bool = True,
) -> dict[str, dict[str, int]]:
    """Replace constituent rows for the current epoch ids exactly.

    This uses delete-then-insert because the fitted constituent set and order can
    change. This prevents stale constituents from lingering under a current
    epoch_id.
    """

    constituent_id_by_epoch_and_short_name: dict[str, dict[str, int]] = {}

    try:
        definition_id_by_short_name = _ensure_constituent_definitions(
            conn,
            constituent_df,
        )

        with conn.cursor() as cur:
            for (time_series_id, epoch_id), _epoch_df in constituent_df.groupby(
                ["time_series_id", "epoch_id"],
                sort=True,
            ):
                cur.execute(
                    """
                    DELETE FROM public.constituent
                    WHERE time_series_id = %s
                      AND epoch_id = %s
                    """,
                    (
                        int(time_series_id),
                        int(epoch_id),
                    ),
                )

            for row in constituent_df.to_dict("records"):
                short_name = str(row["definition_short_name"])

                if short_name not in definition_id_by_short_name:
                    raise RuntimeError(
                        f"Missing constituent_definition id for {short_name!r}."
                    )

                cur.execute(
                    """
                    INSERT INTO public.constituent (
                        amplitude_mm,
                        phase_deg,
                        "order",
                        epoch_id,
                        time_series_id,
                        definition_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        float(row["amplitude_mm"]),
                        float(row["phase_deg"]),
                        int(row["constituent_order"]),
                        int(row["epoch_id"]),
                        int(row["time_series_id"]),
                        int(definition_id_by_short_name[short_name]),
                    ),
                )

                constituent_id = int(cur.fetchone()[0])
                epoch_name = str(row["epoch_name"])

                constituent_id_by_epoch_and_short_name.setdefault(epoch_name, {})[
                    short_name
                ] = constituent_id

        if manage_transaction:
            conn.commit()

    except Exception:
        if manage_transaction:
            conn.rollback()
        raise

    return constituent_id_by_epoch_and_short_name


def _sync_record_constituents_to_database(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
) -> ConstituentSyncResult | None:
    """Resolve, log, and optionally write constituent rows for a record.

    This function intentionally consumes EpochSyncResult. It must not perform
    independent station/version resolution.
    """

    if not DATABASE_POLICY.log_constituent_plan and not DATABASE_POLICY.write_constituents:
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: database constituent sync skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        if DATABASE_POLICY.write_constituents:
            raise RuntimeError(
                f"{record['record_id']}: write_constituents=True requires populated "
                "epoch_sync.epoch_id_by_name. Enable write_epochs=True first."
            )

        log(
            f"{record['record_id']}: database constituent sync skipped | "
            "no epoch IDs available in dry-run/no-write mode"
        )

        return ConstituentSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            constituent_id_by_epoch_and_short_name={},
            missing_definition_short_names=[],
            rows_planned=0,
            rows_written=0,
            write_constituents=bool(DATABASE_POLICY.write_constituents),
        )

    constituent_df = _build_constituent_db_plan_dataframe(
        record=record,
        epoch_sync=epoch_sync,
    )

    CommonUtils, _TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    conn = env_utils.connect_2_tsdb()

    try:
        definition_id_by_short_name = _query_constituent_definition_id_by_short_name(conn)
        planned_short_names = sorted(
            set(constituent_df["definition_short_name"].astype(str))
        )
        missing_definition_short_names = [
            short_name
            for short_name in planned_short_names
            if short_name not in definition_id_by_short_name
        ]

        _log_constituent_db_plan(
            record,
            constituent_df,
            missing_definition_short_names=missing_definition_short_names,
        )

        constituent_id_by_epoch_and_short_name: dict[str, dict[str, int]] = {}

        if DATABASE_POLICY.write_constituents:
            constituent_id_by_epoch_and_short_name = _write_constituents_to_database(
                conn,
                constituent_df,
            )

            written_count = sum(
                len(short_name_ids)
                for short_name_ids in constituent_id_by_epoch_and_short_name.values()
            )

            if written_count != len(constituent_df):
                raise RuntimeError(
                    f"{record['record_id']}: constituent write returned "
                    f"{written_count} row id(s), but planned "
                    f"{len(constituent_df)} constituent row(s)."
                )

            log(
                f"{record['record_id']}: database constituent sync complete | "
                f"constituent_ids={constituent_id_by_epoch_and_short_name}"
            )

        return ConstituentSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            constituent_id_by_epoch_and_short_name=constituent_id_by_epoch_and_short_name,
            missing_definition_short_names=missing_definition_short_names,
            rows_planned=int(len(constituent_df)),
            rows_written=sum(
                len(short_name_ids)
                for short_name_ids in constituent_id_by_epoch_and_short_name.values()
            ),
            write_constituents=bool(DATABASE_POLICY.write_constituents),
        )

    finally:
        conn.close()


def _build_epoch_sync_result_from_target(
    *,
    record: dict[str, Any],
    write_target: EpochWriteTarget,
    epoch_id_by_name: dict[str, int],
) -> EpochSyncResult:
    return EpochSyncResult(
        record_id=str(record["record_id"]),
        station_kind=str(record["station_kind"]),
        source_record_id=write_target.source_record_id,
        input_basis_code=write_target.input_basis_code,
        input_basis_id=int(write_target.input_basis_id),
        time_series_id=int(write_target.time_series_id),
        id_from_source=write_target.id_from_source,
        resolution_rule=write_target.resolution_rule,
        epoch_id_by_name=epoch_id_by_name,
        write_epochs=bool(DATABASE_POLICY.write_epochs),
    )


def _validate_core_table_write_counts(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    datum_df: pd.DataFrame,
    datum_ids: dict[str, dict[str, int]],
    constituent_df: pd.DataFrame,
    constituent_ids: dict[str, dict[str, int]],
) -> None:
    expected_epoch_names = {
        str(epoch_summary["epoch"]["name"])
        for epoch_summary in record["epochs"]
    }

    if set(epoch_sync.epoch_id_by_name) != expected_epoch_names:
        raise RuntimeError(
            f"{record['record_id']}: atomic core write returned unexpected epoch names. "
            f"expected={sorted(expected_epoch_names)}, "
            f"returned={sorted(epoch_sync.epoch_id_by_name)}"
        )

    written_datum_count = sum(len(short_name_ids) for short_name_ids in datum_ids.values())
    if written_datum_count != len(datum_df):
        raise RuntimeError(
            f"{record['record_id']}: atomic core datum write returned "
            f"{written_datum_count} row id(s), but planned {len(datum_df)} row(s)."
        )

    written_constituent_count = sum(
        len(short_name_ids) for short_name_ids in constituent_ids.values()
    )
    if written_constituent_count != len(constituent_df):
        raise RuntimeError(
            f"{record['record_id']}: atomic core constituent write returned "
            f"{written_constituent_count} row id(s), but planned "
            f"{len(constituent_df)} row(s)."
        )

    required_datum_short_names = set(
        DATUM_VALUE_KEY_TO_DEFINITION_SHORT_NAME.values()
    ) | set(DATUM_TIME_KEY_TO_DEFINITION_SHORT_NAME.values())

    planned_datum_short_names_by_epoch = (
        datum_df.groupby("epoch_name")["definition_short_name"]
        .apply(lambda values: {str(value) for value in values})
        .to_dict()
        if not datum_df.empty
        else {}
    )

    for epoch_name in expected_epoch_names:
        planned_short_names = planned_datum_short_names_by_epoch.get(epoch_name, set())
        missing_planned_short_names = sorted(
            required_datum_short_names - planned_short_names
        )

        if missing_planned_short_names:
            raise RuntimeError(
                f"{record['record_id']}: atomic core write planned incomplete "
                f"datum rows for epoch {epoch_name!r}; "
                f"missing={missing_planned_short_names}."
            )

        written_short_names = set(datum_ids.get(epoch_name, {}).keys())
        missing_written_short_names = sorted(
            required_datum_short_names - written_short_names
        )

        if missing_written_short_names:
            raise RuntimeError(
                f"{record['record_id']}: atomic core write returned incomplete "
                f"datum rows for epoch {epoch_name!r}; "
                f"missing={missing_written_short_names}."
            )


def _sync_record_core_tables_to_database(
    record: dict[str, Any],
    *,
    last_update: pd.Timestamp,
) -> tuple[EpochSyncResult | None, DatumSyncResult | None, ConstituentSyncResult | None]:
    """Sync epoch, datum, and constituent rows as one atomic core transaction."""

    if (
        not DATABASE_POLICY.log_epoch_plan
        and not DATABASE_POLICY.log_datum_plan
        and not DATABASE_POLICY.log_constituent_plan
        and not DATABASE_POLICY.write_epochs
        and not DATABASE_POLICY.write_datums
        and not DATABASE_POLICY.write_constituents
    ):
        return None, None, None

    _validate_database_write_policy_or_die()

    CommonUtils, TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    ts_processor = TSDataProcessor()
    conn = env_utils.connect_2_tsdb()

    try:
        input_basis_code = _input_basis_code_for_record(record["station_kind"])
        input_basis_id = ts_processor.query_prediction_input_basis_id_tsdb(
            conn,
            input_basis_code,
        )

        write_target = _resolve_epoch_write_target(
            conn=conn,
            ts_processor=ts_processor,
            record=record,
            input_basis_id=input_basis_id,
        )

        log(
            f"{record['record_id']}: resolved epoch DB target | "
            f"source_record_id={write_target.source_record_id} | "
            f"input_basis={write_target.input_basis_code} | "
            f"time_series_id={write_target.time_series_id} | "
            f"id_from_source={write_target.id_from_source} | "
            f"resolution_rule={write_target.resolution_rule}"
        )

        epoch_df = _build_epoch_db_dataframe(
            record=record,
            time_series_id=write_target.time_series_id,
            input_basis_id=input_basis_id,
            last_update=last_update,
        )

        _log_epoch_db_plan(record)

        if not DATABASE_POLICY.write_epochs:
            epoch_sync = _build_epoch_sync_result_from_target(
                record=record,
                write_target=write_target,
                epoch_id_by_name={},
            )

            log(
                f"{record['record_id']}: database datum sync skipped | "
                "no epoch IDs available in dry-run/no-write mode"
            )
            datum_sync = DatumSyncResult(
                record_id=str(record["record_id"]),
                station_kind=str(record["station_kind"]),
                time_series_id=int(epoch_sync.time_series_id),
                id_from_source=str(epoch_sync.id_from_source),
                input_basis_code=str(epoch_sync.input_basis_code),
                input_basis_id=int(epoch_sync.input_basis_id),
                datum_id_by_epoch_and_short_name={},
                rows_planned=0,
                rows_written=0,
                write_datums=bool(DATABASE_POLICY.write_datums),
            )

            log(
                f"{record['record_id']}: database constituent sync skipped | "
                "no epoch IDs available in dry-run/no-write mode"
            )
            constituent_sync = ConstituentSyncResult(
                record_id=str(record["record_id"]),
                station_kind=str(record["station_kind"]),
                time_series_id=int(epoch_sync.time_series_id),
                id_from_source=str(epoch_sync.id_from_source),
                input_basis_code=str(epoch_sync.input_basis_code),
                input_basis_id=int(epoch_sync.input_basis_id),
                constituent_id_by_epoch_and_short_name={},
                missing_definition_short_names=[],
                rows_planned=0,
                rows_written=0,
                write_constituents=bool(DATABASE_POLICY.write_constituents),
            )

            return epoch_sync, datum_sync, constituent_sync

        try:
            epoch_id_by_name = _upsert_epochs_to_database(conn, epoch_df)

            epoch_sync = _build_epoch_sync_result_from_target(
                record=record,
                write_target=write_target,
                epoch_id_by_name={
                    str(epoch_name): int(epoch_id)
                    for epoch_name, epoch_id in epoch_id_by_name.items()
                },
            )

            expected_epoch_names = {
                str(epoch_summary["epoch"]["name"])
                for epoch_summary in record["epochs"]
            }
            returned_epoch_names = set(epoch_sync.epoch_id_by_name)

            if returned_epoch_names != expected_epoch_names:
                raise RuntimeError(
                    f"{record['record_id']}: epoch upsert returned unexpected "
                    f"epoch names. expected={sorted(expected_epoch_names)}, "
                    f"returned={sorted(returned_epoch_names)}"
                )

            log(
                f"{record['record_id']}: database epoch sync complete | "
                f"epoch_ids={epoch_sync.epoch_id_by_name}"
            )

            definition_id_by_short_name = _query_datum_definition_id_by_short_name(conn)
            datum_df = _build_datum_db_dataframe(
                record=record,
                epoch_sync=epoch_sync,
                definition_id_by_short_name=definition_id_by_short_name,
            )
            _log_datum_db_plan(record, datum_df)

            datum_id_by_epoch_and_short_name = _upsert_datums_to_database(
                conn,
                datum_df,
                manage_transaction=False,
            )

            written_datum_count = sum(
                len(short_name_ids)
                for short_name_ids in datum_id_by_epoch_and_short_name.values()
            )

            log(
                f"{record['record_id']}: database datum sync complete | "
                f"datum_ids={datum_id_by_epoch_and_short_name}"
            )

            constituent_df = _build_constituent_db_plan_dataframe(
                record=record,
                epoch_sync=epoch_sync,
            )
            definition_id_by_short_name = _query_constituent_definition_id_by_short_name(conn)
            planned_short_names = sorted(
                set(constituent_df["definition_short_name"].astype(str))
            )
            missing_definition_short_names = [
                short_name
                for short_name in planned_short_names
                if short_name not in definition_id_by_short_name
            ]

            _log_constituent_db_plan(
                record,
                constituent_df,
                missing_definition_short_names=missing_definition_short_names,
            )

            constituent_id_by_epoch_and_short_name = _write_constituents_to_database(
                conn,
                constituent_df,
                manage_transaction=False,
            )

            _validate_core_table_write_counts(
                record=record,
                epoch_sync=epoch_sync,
                datum_df=datum_df,
                datum_ids=datum_id_by_epoch_and_short_name,
                constituent_df=constituent_df,
                constituent_ids=constituent_id_by_epoch_and_short_name,
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        log(
            f"{record['record_id']}: database constituent sync complete | "
            f"constituent_ids={constituent_id_by_epoch_and_short_name}"
        )

        datum_sync = DatumSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            datum_id_by_epoch_and_short_name=datum_id_by_epoch_and_short_name,
            rows_planned=int(len(datum_df)),
            rows_written=int(written_datum_count),
            write_datums=bool(DATABASE_POLICY.write_datums),
        )

        constituent_sync = ConstituentSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            constituent_id_by_epoch_and_short_name=constituent_id_by_epoch_and_short_name,
            missing_definition_short_names=missing_definition_short_names,
            rows_planned=int(len(constituent_df)),
            rows_written=sum(
                len(short_name_ids)
                for short_name_ids in constituent_id_by_epoch_and_short_name.values()
            ),
            write_constituents=bool(DATABASE_POLICY.write_constituents),
        )

        return epoch_sync, datum_sync, constituent_sync

    finally:
        conn.close()


def _current_record_epoch_names(record: dict[str, Any]) -> list[str]:
    """Return selected epoch names for one processed record."""

    return sorted(
        str(epoch_summary["epoch"]["name"])
        for epoch_summary in record.get("epochs", [])
    )


def _query_stale_recent_epochs_for_cleanup(
    conn,
    *,
    epoch_sync: EpochSyncResult,
    current_epoch_names: list[str],
) -> list[dict[str, Any]]:
    """Find stale RECENT_* epochs for one time_series/input_basis target."""

    rows = _fetchall_dicts(
        conn,
        """
        SELECT
            e.id AS epoch_id,
            e.name AS epoch_name,
            e."primary" AS primary,
            e.is_prediction_basis AS is_prediction_basis,
            COALESCE(d.datum_rows, 0) AS datum_rows,
            COALESCE(c.constituent_rows, 0) AS constituent_rows,
            COALESCE(tp.tide_prediction_rows, 0) AS tide_prediction_rows,
            COALESCE(hftp.hf_tide_prediction_rows, 0) AS hf_tide_prediction_rows,
            COALESCE(hlp.high_low_prediction_rows, 0) AS high_low_prediction_rows
        FROM public.epoch e
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS datum_rows
            FROM public.datum d
            WHERE d.epoch_id = e.id
        ) d ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS constituent_rows
            FROM public.constituent c
            WHERE c.epoch_id = e.id
        ) c ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS tide_prediction_rows
            FROM public.tide_prediction tp
            WHERE tp.epoch_id = e.id
        ) tp ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS hf_tide_prediction_rows
            FROM public.hf_tide_prediction hftp
            WHERE hftp.epoch_id = e.id
        ) hftp ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS high_low_prediction_rows
            FROM public.high_low_prediction hlp
            WHERE hlp.epoch_id = e.id
        ) hlp ON TRUE
        WHERE e.time_series_id = %s
          AND e.input_basis_id = %s
          AND COALESCE(e."source", '') != 'legacy'
          AND LEFT(e.name, 7) = 'RECENT_'
          AND NOT (e.name = ANY(%s::text[]))
        ORDER BY e.id
        """,
        (
            int(epoch_sync.time_series_id),
            int(epoch_sync.input_basis_id),
            list(current_epoch_names),
        ),
    )

    stale_epochs: list[dict[str, Any]] = []
    for row in rows:
        stale_epochs.append(
            {
                "epoch_id": int(row["epoch_id"]),
                "epoch_name": str(row["epoch_name"]),
                "primary": bool(row["primary"]),
                "is_prediction_basis": bool(row["is_prediction_basis"]),
                "datum_rows": int(row["datum_rows"]),
                "constituent_rows": int(row["constituent_rows"]),
                "tide_prediction_rows": int(row["tide_prediction_rows"]),
                "hf_tide_prediction_rows": int(row["hf_tide_prediction_rows"]),
                "high_low_prediction_rows": int(row["high_low_prediction_rows"]),
            }
        )

    return stale_epochs


def _delete_stale_recent_epochs(
    conn,
    *,
    epoch_sync: EpochSyncResult,
    stale_epoch_ids: list[int],
) -> dict[str, int]:
    """Delete stale RECENT_* epochs and all child rows."""

    if not stale_epoch_ids:
        return {
            "high_low_prediction_rows_deleted": 0,
            "hf_tide_prediction_rows_deleted": 0,
            "tide_prediction_rows_deleted": 0,
            "constituent_rows_deleted": 0,
            "datum_rows_deleted": 0,
            "epoch_rows_deleted": 0,
        }

    deleted = {
        "high_low_prediction_rows_deleted": 0,
        "hf_tide_prediction_rows_deleted": 0,
        "tide_prediction_rows_deleted": 0,
        "constituent_rows_deleted": 0,
        "datum_rows_deleted": 0,
        "epoch_rows_deleted": 0,
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.high_low_prediction
            WHERE epoch_id = ANY(%s::bigint[])
            """,
            (stale_epoch_ids,),
        )
        deleted["high_low_prediction_rows_deleted"] = int(cur.rowcount)

        cur.execute(
            """
            DELETE FROM public.hf_tide_prediction
            WHERE epoch_id = ANY(%s::bigint[])
            """,
            (stale_epoch_ids,),
        )
        deleted["hf_tide_prediction_rows_deleted"] = int(cur.rowcount)

        cur.execute(
            """
            DELETE FROM public.tide_prediction
            WHERE epoch_id = ANY(%s::bigint[])
            """,
            (stale_epoch_ids,),
        )
        deleted["tide_prediction_rows_deleted"] = int(cur.rowcount)

        cur.execute(
            """
            DELETE FROM public.constituent
            WHERE epoch_id = ANY(%s::bigint[])
            """,
            (stale_epoch_ids,),
        )
        deleted["constituent_rows_deleted"] = int(cur.rowcount)

        cur.execute(
            """
            DELETE FROM public.datum
            WHERE epoch_id = ANY(%s::bigint[])
            """,
            (stale_epoch_ids,),
        )
        deleted["datum_rows_deleted"] = int(cur.rowcount)

        cur.execute(
            """
            DELETE FROM public.epoch
            WHERE id = ANY(%s::bigint[])
              AND time_series_id = %s
              AND input_basis_id = %s
              AND COALESCE("source", '') != 'legacy'
              AND LEFT(name, 7) = 'RECENT_'
            """,
            (
                stale_epoch_ids,
                int(epoch_sync.time_series_id),
                int(epoch_sync.input_basis_id),
            ),
        )
        deleted["epoch_rows_deleted"] = int(cur.rowcount)

    return deleted


def _run_stale_recent_epoch_cleanup(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
) -> StaleRecentEpochCleanupResult | None:
    """Remove old rolling RECENT_* epochs for one processed DB target."""

    if (
        not DATABASE_POLICY.auto_cleanup_stale_recent_epochs
        and not DATABASE_POLICY.write_stale_recent_epoch_cleanup
    ):
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: stale RECENT epoch cleanup skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        log(
            f"{record['record_id']}: stale RECENT epoch cleanup skipped | "
            "no epoch IDs available"
        )
        return None

    current_epoch_names = _current_record_epoch_names(record)

    with _tsdb_connection() as (conn, _ts_processor):
        stale_epochs = _query_stale_recent_epochs_for_cleanup(
            conn,
            epoch_sync=epoch_sync,
            current_epoch_names=current_epoch_names,
        )

        if DATABASE_POLICY.log_stale_recent_epoch_cleanup_plan:
            log(
                f"{record['record_id']}: stale RECENT epoch cleanup plan | "
                f"time_series_id={epoch_sync.time_series_id} | "
                f"id_from_source={epoch_sync.id_from_source} | "
                f"input_basis={epoch_sync.input_basis_code} | "
                f"stale_epochs={len(stale_epochs)} | "
                f"write_cleanup={DATABASE_POLICY.write_stale_recent_epoch_cleanup}"
            )

            for stale_epoch in stale_epochs:
                log(
                    f"{record['record_id']}: "
                    f"{'DELETE' if DATABASE_POLICY.write_stale_recent_epoch_cleanup else 'WOULD DELETE'} "
                    "stale RECENT epoch | "
                    f"epoch_id={stale_epoch['epoch_id']} | "
                    f"name={stale_epoch['epoch_name']} | "
                    f"datums={stale_epoch['datum_rows']} | "
                    f"constituents={stale_epoch['constituent_rows']} | "
                    f"tide_predictions={stale_epoch['tide_prediction_rows']} | "
                    f"hf_tide_predictions={stale_epoch['hf_tide_prediction_rows']} | "
                    f"high_low_predictions={stale_epoch['high_low_prediction_rows']}"
                )

        deleted = {
            "high_low_prediction_rows_deleted": 0,
            "hf_tide_prediction_rows_deleted": 0,
            "tide_prediction_rows_deleted": 0,
            "constituent_rows_deleted": 0,
            "datum_rows_deleted": 0,
            "epoch_rows_deleted": 0,
        }

        if DATABASE_POLICY.write_stale_recent_epoch_cleanup and stale_epochs:
            try:
                deleted = _delete_stale_recent_epochs(
                    conn,
                    epoch_sync=epoch_sync,
                    stale_epoch_ids=[
                        int(stale_epoch["epoch_id"])
                        for stale_epoch in stale_epochs
                    ],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            log(
                f"{record['record_id']}: stale RECENT epoch cleanup complete | "
                f"epochs_deleted={deleted['epoch_rows_deleted']} | "
                f"datums_deleted={deleted['datum_rows_deleted']} | "
                f"constituents_deleted={deleted['constituent_rows_deleted']} | "
                f"tide_predictions_deleted={deleted['tide_prediction_rows_deleted']} | "
                f"hf_tide_predictions_deleted={deleted['hf_tide_prediction_rows_deleted']} | "
                f"high_low_predictions_deleted={deleted['high_low_prediction_rows_deleted']}"
            )

        return StaleRecentEpochCleanupResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            current_epoch_names=current_epoch_names,
            stale_epochs=stale_epochs,
            datum_rows_deleted=int(deleted["datum_rows_deleted"]),
            constituent_rows_deleted=int(deleted["constituent_rows_deleted"]),
            tide_prediction_rows_deleted=int(deleted["tide_prediction_rows_deleted"]),
            hf_tide_prediction_rows_deleted=int(
                deleted["hf_tide_prediction_rows_deleted"]
            ),
            high_low_prediction_rows_deleted=int(
                deleted["high_low_prediction_rows_deleted"]
            ),
            epoch_rows_deleted=int(deleted["epoch_rows_deleted"]),
            write_cleanup=bool(DATABASE_POLICY.write_stale_recent_epoch_cleanup),
        )


def _database_prediction_value_from_mm(value_mm: Any) -> float:
    return round(
        float(value_mm) * DATABASE_POLICY.prediction_value_mm_to_database_meters,
        DATABASE_POLICY.prediction_value_decimal_places,
    )


def _query_temporal_resolution_id(conn, resolution_code: str) -> int:
    row = _fetchone_dict(
        conn,
        """
        SELECT id
        FROM public.temporal_resolution
        WHERE resolution = %s
        """,
        (str(resolution_code),),
    )

    if row is None:
        raise RuntimeError(
            f"No temporal_resolution row found for resolution={resolution_code!r}."
        )

    return int(row["id"])


def _prediction_time_bounds_from_summary(prediction_summary: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    return (
        pd.Timestamp(prediction_summary["start"]),
        pd.Timestamp(prediction_summary["end"]),
    )


def _to_database_window_timestamp(value: Any) -> pd.Timestamp:
    """Normalize DB/materialized-view times to UTC-naive pandas timestamps.

    Generated prediction products in this pipeline are timezone-naive. PostgreSQL
    timestamptz values may come back timezone-aware. Converting aware timestamps
    to UTC-naive keeps comparisons aligned with the generated UTC-like products.
    """

    ts = pd.Timestamp(value)

    if ts.tzinfo is not None:
        return ts.tz_convert(None)

    return ts


def _record_quality_short_name_for_prediction_window(
    epoch_sync: EpochSyncResult,
) -> str:
    if epoch_sync.input_basis_code == DATABASE_POLICY.fd_input_basis_code:
        return DATABASE_POLICY.fd_record_quality_short_name

    if epoch_sync.input_basis_code == DATABASE_POLICY.rq_input_basis_code:
        return DATABASE_POLICY.rq_record_quality_short_name

    raise RuntimeError(
        f"Unsupported input_basis_code for prediction window lookup: "
        f"{epoch_sync.input_basis_code!r}."
    )


def _query_prediction_valid_range(
    conn,
    *,
    time_series_id: int,
    record_quality_short_name: str,
    temporal_resolution_code: str,
) -> dict[str, Any]:
    """Query the DB-authoritative prediction-valid range for a target.

    For RQ products, this bounds writes to the exact research-quality
    time_series range. For FD cleanup, this bounds superseded best_available
    products to their historical operational ranges.
    """

    rows = _fetchall_dicts(
        conn,
        """
        SELECT
          r.time_series_id,
          r.id_from_source,
          r.station_id,
          r.record_id,
          r.priority,
          r.quality_id,
          r.quality_level,
          r.resolution_id,
          r.date_begin,
          r.date_end,
          r.last_update
        FROM public.date_range_by_time_series_quality r
        JOIN public.record_quality q
          ON q.id = r.quality_id
        JOIN public.temporal_resolution tr
          ON tr.id = r.resolution_id
        WHERE r.time_series_id = %s
          AND lower(q.short_name) = lower(%s)
          AND tr.resolution = %s
        ORDER BY r.date_begin, r.date_end, r.priority
        """,
        (
            int(time_series_id),
            str(record_quality_short_name),
            str(temporal_resolution_code),
        ),
    )

    if not rows:
        raise RuntimeError(
            "No prediction-valid range found in "
            "public.date_range_by_time_series_quality for "
            f"time_series_id={time_series_id}, "
            f"record_quality_short_name={record_quality_short_name!r}, "
            f"temporal_resolution_code={temporal_resolution_code!r}."
        )

    if len(rows) > 1:
        raise RuntimeError(
            "Multiple prediction-valid ranges found in "
            "public.date_range_by_time_series_quality for "
            f"time_series_id={time_series_id}, "
            f"record_quality_short_name={record_quality_short_name!r}, "
            f"temporal_resolution_code={temporal_resolution_code!r}: {rows}"
        )

    row = rows[0]
    row["date_begin"] = _to_database_window_timestamp(row["date_begin"])
    row["date_end"] = _to_database_window_timestamp(row["date_end"])
    row["last_update"] = _to_database_window_timestamp(row["last_update"])

    return row


def _resolve_record_prediction_db_window(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult | None,
) -> PredictionDbWindow | None:
    """Resolve DB metadata needed by tide/HF/high-low prediction writers.

    This should be called once per processed record and passed to both
    prediction writers. It keeps date_range_by_time_series_quality lookups short
    and prevents holding a DB connection open during expensive high/low
    generation.
    """

    if epoch_sync is None or not epoch_sync.epoch_id_by_name:
        return None

    with _tsdb_connection() as (conn, _ts_processor):
        resolution_id = _query_temporal_resolution_id(
            conn,
            DATABASE_POLICY.hourly_temporal_resolution_code,
        )

        if str(record["station_kind"]).upper() == "FD":
            return PredictionDbWindow(
                resolution_id=int(resolution_id),
                temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
                record_quality_short_name=None,
                date_begin=None,
                date_end=None,
                date_range_last_update=None,
            )

        record_quality_short_name = _record_quality_short_name_for_prediction_window(
            epoch_sync
        )
        valid_range = _query_prediction_valid_range(
            conn,
            time_series_id=epoch_sync.time_series_id,
            record_quality_short_name=record_quality_short_name,
            temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
        )

        return PredictionDbWindow(
            resolution_id=int(resolution_id),
            temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
            record_quality_short_name=record_quality_short_name,
            date_begin=pd.Timestamp(valid_range["date_begin"]),
            date_end=pd.Timestamp(valid_range["date_end"]),
            date_range_last_update=pd.Timestamp(valid_range["last_update"]),
        )


def _allowed_prediction_window_from_db_window(
    *,
    generated_start: pd.Timestamp,
    generated_end: pd.Timestamp,
    prediction_window: PredictionDbWindow,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Intersect a generated prediction window with a resolved DB-valid window.

    FD/current best_available records have no DB-valid bounds here and return
    the generated operational window unchanged.
    """

    if prediction_window.date_begin is None or prediction_window.date_end is None:
        return pd.Timestamp(generated_start), pd.Timestamp(generated_end)

    return (
        max(pd.Timestamp(generated_start), pd.Timestamp(prediction_window.date_begin)),
        min(pd.Timestamp(generated_end), pd.Timestamp(prediction_window.date_end)),
    )


def _allowed_hourly_prediction_window(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    prediction_summary: dict[str, Any],
    prediction_window: PredictionDbWindow,
) -> tuple[pd.Timestamp, pd.Timestamp, list[str]]:
    """Return DB insert window for a generated hourly prediction product.

    FD/best_available current-target products keep the generated long-future
    operational window.

    RQ products are bounded by the previously resolved rq/hourly
    date_range_by_time_series_quality window.
    """

    generated_start, generated_end = _prediction_time_bounds_from_summary(
        prediction_summary
    )
    notes: list[str] = []

    if str(record["station_kind"]).upper() == "FD":
        return generated_start, generated_end, notes

    allowed_start, allowed_end = _allowed_prediction_window_from_db_window(
        generated_start=generated_start,
        generated_end=generated_end,
        prediction_window=prediction_window,
    )

    record_quality_short_name = prediction_window.record_quality_short_name or "unknown"

    if allowed_end < allowed_start:
        notes.append(
            f"No overlap between generated hourly prediction window "
            f"{generated_start} to {generated_end} and DB {record_quality_short_name}/"
            f"{prediction_window.temporal_resolution_code} valid range "
            f"{prediction_window.date_begin} to {prediction_window.date_end}."
        )
    else:
        notes.append(
            f"{record_quality_short_name.upper()} hourly prediction bounded by "
            f"date_range_by_time_series_quality: {prediction_window.date_begin} "
            f"to {prediction_window.date_end}."
        )

    return allowed_start, allowed_end, notes


def _build_hourly_tide_prediction_db_dataframe(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    prediction_window: PredictionDbWindow,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    if not epoch_sync.epoch_id_by_name:
        raise RuntimeError(
            f"{record['record_id']}: cannot build tide_prediction rows because "
            "epoch_sync.epoch_id_by_name is empty. Enable write_epochs=True "
            "before write_tide_predictions=True."
        )

    hourly_frames = record.get("_hourly_prediction_frames") or {}
    rows: list[pd.DataFrame] = []
    delete_windows: list[dict[str, Any]] = []
    notes: list[str] = []

    for prediction_summary in record.get("hourly_predictions") or []:
        epoch_name = str(prediction_summary["epoch"])
        prediction_key = str(prediction_summary["prediction_key"])

        if epoch_name not in epoch_sync.epoch_id_by_name:
            raise RuntimeError(
                f"{record['record_id']}: hourly prediction epoch {epoch_name!r} "
                f"is missing from epoch_sync.epoch_id_by_name={epoch_sync.epoch_id_by_name}."
            )

        if prediction_key not in hourly_frames:
            raise RuntimeError(
                f"{record['record_id']}: missing hourly prediction frame for "
                f"prediction_key={prediction_key!r}."
            )

        generated_start, generated_end = _prediction_time_bounds_from_summary(
            prediction_summary
        )
        allowed_start, allowed_end, window_notes = _allowed_hourly_prediction_window(
            record=record,
            epoch_sync=epoch_sync,
            prediction_summary=prediction_summary,
            prediction_window=prediction_window,
        )
        notes.extend(window_notes)

        epoch_id = int(epoch_sync.epoch_id_by_name[epoch_name])

        # Delete the full generated window, not just the allowed insert window.
        # This cleans up any earlier accidental long-future writes if the DB
        # policy later trims the insert window.
        delete_windows.append(
            {
                "time_series_id": int(epoch_sync.time_series_id),
                "epoch_id": epoch_id,
                "resolution_id": int(prediction_window.resolution_id),
                "delete_start": generated_start,
                "delete_end": generated_end,
                "insert_start": allowed_start,
                "insert_end": allowed_end,
                "epoch_name": epoch_name,
            }
        )

        frame = hourly_frames[prediction_key].copy()
        frame["time"] = pd.to_datetime(frame["time"])
        frame = frame[
            (frame["time"] >= allowed_start)
            & (frame["time"] <= allowed_end)
        ].copy()

        if frame.empty:
            notes.append(
                f"No hourly prediction rows remained after DB window filtering for "
                f"{record['record_id']} {epoch_name}."
            )
            continue

        if "prediction_mm" not in frame.columns:
            raise RuntimeError(
                f"{record['record_id']} {epoch_name}: hourly prediction frame "
                "does not contain prediction_mm."
            )

        frame = pd.DataFrame(
            {
                "time": frame["time"],
                "value": frame["prediction_mm"].map(_database_prediction_value_from_mm),
                "epoch_id": epoch_id,
                "resolution_id": int(prediction_window.resolution_id),
                "time_series_id": int(epoch_sync.time_series_id),
                "epoch_name": epoch_name,
            }
        )

        rows.append(frame)

    if not rows:
        return pd.DataFrame(
            columns=[
                "time",
                "value",
                "epoch_id",
                "resolution_id",
                "time_series_id",
                "epoch_name",
            ]
        ), delete_windows, notes

    return pd.concat(rows, ignore_index=True), delete_windows, notes


def _log_tide_prediction_db_plan(
    record: dict[str, Any],
    prediction_df: pd.DataFrame,
    *,
    delete_windows: list[dict[str, Any]],
    notes: list[str],
) -> None:
    log(
        f"{record['record_id']}: database tide_prediction sync plan | "
        f"hourly_rows={len(prediction_df)} | "
        f"delete_windows={len(delete_windows)} | "
        f"value_units=meters | "
        f"write_tide_predictions={DATABASE_POLICY.write_tide_predictions}"
    )

    for note in notes:
        log(f"{record['record_id']}: tide_prediction note | {note}")

    for window in delete_windows:
        log(
            f"{record['record_id']}: "
            f"{'REPLACE' if DATABASE_POLICY.write_tide_predictions else 'WOULD REPLACE'} "
            f"hourly tide_prediction rows for epoch {window['epoch_name']} | "
            f"delete_window={window['delete_start']} to {window['delete_end']} | "
            f"insert_window={window['insert_start']} to {window['insert_end']}"
        )


def _delete_tide_prediction_windows(
    conn,
    delete_windows: list[dict[str, Any]],
) -> int:
    rows_deleted = 0

    with conn.cursor() as cur:
        for window in delete_windows:
            cur.execute(
                """
                DELETE FROM public.tide_prediction
                WHERE time_series_id = %s
                  AND epoch_id = %s
                  AND resolution_id = %s
                  AND "time" >= %s
                  AND "time" <= %s
                """,
                (
                    int(window["time_series_id"]),
                    int(window["epoch_id"]),
                    int(window["resolution_id"]),
                    pd.Timestamp(window["delete_start"]).to_pydatetime(),
                    pd.Timestamp(window["delete_end"]).to_pydatetime(),
                ),
            )
            rows_deleted += int(cur.rowcount)

    return rows_deleted


def _prediction_insert_batch_size() -> int:
    batch_size = int(DATABASE_POLICY.prediction_insert_batch_size)
    if batch_size <= 0:
        raise ValueError(
            "DATABASE_POLICY.prediction_insert_batch_size must be a positive integer."
        )
    return batch_size


def _iter_dataframe_batches(df: pd.DataFrame, batch_size: int):
    for start in range(0, len(df), batch_size):
        yield df.iloc[start : start + batch_size]


def _insert_tide_prediction_rows(
    conn,
    prediction_df: pd.DataFrame,
) -> int:
    if prediction_df.empty:
        return 0

    batch_size = _prediction_insert_batch_size()

    insert_sql = """
        INSERT INTO public.tide_prediction (
            "time",
            value,
            epoch_id,
            resolution_id,
            time_series_id
        )
        VALUES %s
        ON CONFLICT (time_series_id, epoch_id, resolution_id, "time")
        DO UPDATE SET
            value = EXCLUDED.value
    """

    rows_written = 0
    insert_columns = [
        "time",
        "value",
        "epoch_id",
        "resolution_id",
        "time_series_id",
    ]

    with conn.cursor() as cur:
        for batch_df in _iter_dataframe_batches(prediction_df, batch_size):
            rows = [
                (
                    pd.Timestamp(time_value).to_pydatetime(),
                    float(value),
                    int(epoch_id),
                    int(resolution_id),
                    int(time_series_id),
                )
                for (
                    time_value,
                    value,
                    epoch_id,
                    resolution_id,
                    time_series_id,
                ) in batch_df.loc[:, insert_columns].itertuples(index=False, name=None)
            ]

            if not rows:
                continue

            execute_values(
                cur,
                insert_sql,
                rows,
                page_size=batch_size,
            )

            rows_written += len(rows)

    return rows_written


def _sync_record_tide_predictions_to_database(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
    prediction_window: PredictionDbWindow | None = None,
) -> TidePredictionSyncResult | None:
    """Resolve, log, and optionally write hourly tide_prediction rows.

    This function intentionally consumes EpochSyncResult. It must not perform
    independent station/version resolution.
    """

    if (
        not DATABASE_POLICY.log_tide_prediction_plan
        and not DATABASE_POLICY.write_tide_predictions
    ):
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: database tide_prediction sync skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        if DATABASE_POLICY.write_tide_predictions:
            raise RuntimeError(
                f"{record['record_id']}: write_tide_predictions=True requires "
                "populated epoch_sync.epoch_id_by_name. Enable write_epochs=True first."
            )

        log(
            f"{record['record_id']}: database tide_prediction sync skipped | "
            "no epoch IDs available in dry-run/no-write mode"
        )

        return TidePredictionSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            resolution_id=0,
            rows_planned=0,
            rows_deleted=0,
            rows_written=0,
            rows_skipped=0,
            write_tide_predictions=bool(DATABASE_POLICY.write_tide_predictions),
            notes=["No epoch IDs available."],
        )

    if prediction_window is None:
        prediction_window = _resolve_record_prediction_db_window(
            record=record,
            epoch_sync=epoch_sync,
        )

    if prediction_window is None:
        raise RuntimeError(
            f"{record['record_id']}: could not resolve prediction DB window."
        )

    prediction_df, delete_windows, notes = _build_hourly_tide_prediction_db_dataframe(
        record=record,
        epoch_sync=epoch_sync,
        prediction_window=prediction_window,
    )

    _log_tide_prediction_db_plan(
        record,
        prediction_df,
        delete_windows=delete_windows,
        notes=notes,
    )

    rows_deleted = 0
    rows_written = 0

    if DATABASE_POLICY.write_tide_predictions:
        with _tsdb_connection() as (conn, _ts_processor):
            try:
                rows_deleted = _delete_tide_prediction_windows(
                    conn,
                    delete_windows,
                )
                rows_written = _insert_tide_prediction_rows(
                    conn,
                    prediction_df,
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

        log(
            f"{record['record_id']}: database tide_prediction sync complete | "
            f"rows_deleted={rows_deleted} | rows_written={rows_written}"
        )

    return TidePredictionSyncResult(
        record_id=str(record["record_id"]),
        station_kind=str(record["station_kind"]),
        time_series_id=int(epoch_sync.time_series_id),
        id_from_source=str(epoch_sync.id_from_source),
        input_basis_code=str(epoch_sync.input_basis_code),
        input_basis_id=int(epoch_sync.input_basis_id),
        resolution_id=int(prediction_window.resolution_id),
        rows_planned=int(len(prediction_df)),
        rows_deleted=int(rows_deleted),
        rows_written=int(rows_written),
        rows_skipped=0,
        write_tide_predictions=bool(DATABASE_POLICY.write_tide_predictions),
        notes=notes,
    )


def _query_hf_resolution_target(
    conn,
    *,
    time_series_id: int,
) -> HfResolutionTarget | None:
    """Return the latest observed HF resolution for one time series.

    This preserves the legacy Level 3 rule: the derived prediction grid follows
    the resolution attached to the latest row in public.hf_time_series_data.
    """

    rows = _fetchall_dicts(
        conn,
        """
        WITH latest AS (
            SELECT h."time"
            FROM public.hf_time_series_data h
            WHERE h.time_series_id = %s
            ORDER BY h."time" DESC
            LIMIT 1
        )
        SELECT DISTINCT
            h.resolution_id,
            tr.resolution AS temporal_resolution_code,
            h."time" AS latest_time
        FROM public.hf_time_series_data h
        JOIN public.temporal_resolution tr
          ON tr.id = h.resolution_id
        WHERE h.time_series_id = %s
          AND h."time" = (SELECT "time" FROM latest)
        ORDER BY h.resolution_id
        """,
        (int(time_series_id), int(time_series_id)),
    )

    if not rows:
        return None

    if len(rows) > 1:
        raise RuntimeError(
            "Multiple HF resolutions occur at the latest observed timestamp for "
            f"time_series_id={time_series_id}: {rows}. Refusing to choose one."
        )

    row = rows[0]
    target = HfResolutionTarget(
        resolution_id=int(row["resolution_id"]),
        temporal_resolution_code=str(row["temporal_resolution_code"]),
        latest_time=_to_database_window_timestamp(row["latest_time"]),
    )

    # Validate now so a malformed DB resolution fails before any delete/write.
    minute_resolution_timedelta(target.temporal_resolution_code)
    return target


def _configured_hf_prediction_window() -> tuple[pd.Timestamp, pd.Timestamp]:
    start = _to_database_window_timestamp(DATABASE_POLICY.hf_prediction_start)
    end = _to_database_window_timestamp(DATABASE_POLICY.hf_prediction_end)

    if end < start:
        raise ValueError(
            "DATABASE_POLICY.hf_prediction_end precedes hf_prediction_start: "
            f"{start} to {end}."
        )

    if int(DATABASE_POLICY.hf_prediction_chunk_days) <= 0:
        raise ValueError(
            "DATABASE_POLICY.hf_prediction_chunk_days must be a positive integer."
        )

    return start, end


def _build_hf_tide_prediction_plans(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    prediction_window: PredictionDbWindow,
    resolution_target: HfResolutionTarget | None,
) -> tuple[list[HfTidePredictionPlan], list[str]]:
    """Build one HF plan for every saved hourly prediction product.

    The insert window is the intersection of:
      1. the generated hourly product,
      2. the regular tide-prediction DB-authorized window, and
      3. the configured 2016-2035 HF product window.

    A plan is retained even when that intersection is empty so a rerun can
    reconcile/delete any stale HF rows for the same product identity.
    """

    if not epoch_sync.epoch_id_by_name:
        raise RuntimeError(
            f"{record['record_id']}: cannot build hf_tide_prediction plans because "
            "epoch_sync.epoch_id_by_name is empty. Enable write_epochs=True "
            "before write_hf_tide_predictions=True."
        )

    hourly_frames = record.get("_hourly_prediction_frames") or {}
    hf_start, hf_end = _configured_hf_prediction_window()
    interval = (
        None
        if resolution_target is None
        else minute_resolution_timedelta(
            resolution_target.temporal_resolution_code
        )
    )
    plans: list[HfTidePredictionPlan] = []
    notes: list[str] = []
    seen_epoch_names: set[str] = set()

    prediction_summaries = record.get("hourly_predictions") or []
    if not prediction_summaries:
        notes.append("No saved hourly prediction products available for HF generation.")

    for prediction_summary in prediction_summaries:
        epoch_name = str(prediction_summary["epoch"])
        prediction_key = str(prediction_summary["prediction_key"])

        if epoch_name in seen_epoch_names:
            raise RuntimeError(
                f"{record['record_id']}: duplicate saved hourly prediction product "
                f"for epoch {epoch_name!r}; HF database identity would collide."
            )
        seen_epoch_names.add(epoch_name)

        if epoch_name not in epoch_sync.epoch_id_by_name:
            raise RuntimeError(
                f"{record['record_id']}: HF prediction epoch {epoch_name!r} "
                f"is missing from epoch_sync.epoch_id_by_name="
                f"{epoch_sync.epoch_id_by_name}."
            )

        if prediction_key not in hourly_frames:
            raise RuntimeError(
                f"{record['record_id']}: missing hourly prediction frame for HF "
                f"prediction_key={prediction_key!r}."
            )

        allowed_start, allowed_end, window_notes = _allowed_hourly_prediction_window(
            record=record,
            epoch_sync=epoch_sync,
            prediction_summary=prediction_summary,
            prediction_window=prediction_window,
        )
        notes.extend(window_notes)

        frame = hourly_frames[prediction_key]
        if frame.empty:
            raise RuntimeError(
                f"{record['record_id']} {epoch_name}: hourly prediction frame is "
                "empty and cannot be used for HF interpolation."
            )
        if not {"time", "prediction_mm"}.issubset(frame.columns):
            raise RuntimeError(
                f"{record['record_id']} {epoch_name}: hourly prediction frame "
                "must contain time and prediction_mm."
            )

        frame_times = pd.to_datetime(frame["time"], errors="raise")
        if getattr(frame_times.dt, "tz", None) is not None:
            frame_times = frame_times.dt.tz_convert("UTC").dt.tz_localize(None)
        source_start = pd.Timestamp(frame_times.min())
        source_end = pd.Timestamp(frame_times.max())

        insert_start = max(
            pd.Timestamp(allowed_start),
            hf_start,
            source_start,
        )
        insert_end = min(
            pd.Timestamp(allowed_end),
            hf_end,
            source_end,
        )

        if insert_end < insert_start:
            rows_planned = 0
            plan_insert_start: pd.Timestamp | None = None
            plan_insert_end: pd.Timestamp | None = None
            notes.append(
                f"No HF overlap for {record['record_id']} {epoch_name}: regular "
                f"prediction window={allowed_start} to {allowed_end}; configured "
                f"HF window={hf_start} to {hf_end}. No HF rows will be inserted."
            )
        else:
            plan_insert_start = insert_start
            plan_insert_end = insert_end
            if interval is None:
                rows_planned = 0
                notes.append(
                    f"HF overlap exists for {epoch_name} from {insert_start} to "
                    f"{insert_end}, but no target HF resolution has been resolved."
                )
            else:
                rows_planned = hf_prediction_row_count(
                    insert_start,
                    insert_end,
                    interval,
                )
                notes.append(
                    f"HF prediction for {epoch_name} will use full-precision hourly "
                    f"values and natural cubic-spline interpolation at "
                    f"{resolution_target.temporal_resolution_code}: "
                    f"{insert_start} to {insert_end}."
                )

        plans.append(
            HfTidePredictionPlan(
                epoch_name=epoch_name,
                epoch_id=int(epoch_sync.epoch_id_by_name[epoch_name]),
                time_series_id=int(epoch_sync.time_series_id),
                resolution_id=(
                    None
                    if resolution_target is None
                    else int(resolution_target.resolution_id)
                ),
                temporal_resolution_code=(
                    None
                    if resolution_target is None
                    else str(resolution_target.temporal_resolution_code)
                ),
                interval=interval,
                # Reconcile the complete configured HF product window for this
                # time-series/epoch, even when the current hourly product has no
                # overlap. The delete step removes all older resolution variants.
                delete_start=hf_start,
                delete_end=hf_end,
                insert_start=plan_insert_start,
                insert_end=plan_insert_end,
                rows_planned=int(rows_planned),
                hourly_frame=frame,
            )
        )

    return plans, notes


def _log_hf_tide_prediction_db_plan(
    record: dict[str, Any],
    *,
    resolution_target: HfResolutionTarget | None,
    plans: list[HfTidePredictionPlan],
    notes: list[str],
) -> None:
    rows_planned = sum(plan.rows_planned for plan in plans)
    resolution_text = (
        "none"
        if resolution_target is None
        else (
            f"{resolution_target.temporal_resolution_code} "
            f"(resolution_id={resolution_target.resolution_id}, "
            f"latest_hf_time={resolution_target.latest_time})"
        )
    )

    log(
        f"{record['record_id']}: database hf_tide_prediction sync plan | "
        f"rows={rows_planned} | products={len(plans)} | "
        f"resolution={resolution_text} | value_units=meters | "
        f"write_hf_tide_predictions={DATABASE_POLICY.write_hf_tide_predictions}"
    )

    for note in notes:
        log(f"{record['record_id']}: hf_tide_prediction note | {note}")

    for plan in plans:
        log(
            f"{record['record_id']}: "
            f"{'REPLACE' if DATABASE_POLICY.write_hf_tide_predictions else 'WOULD REPLACE'} "
            f"HF tide_prediction rows for epoch {plan.epoch_name} | "
            f"delete_window={plan.delete_start} to {plan.delete_end} | "
            f"insert_window={plan.insert_start} to {plan.insert_end} | "
            f"rows={plan.rows_planned}"
        )


def _delete_hf_tide_prediction_windows(
    conn,
    plans: list[HfTidePredictionPlan],
) -> int:
    rows_deleted = 0

    with conn.cursor() as cur:
        for plan in plans:
            cur.execute(
                """
                DELETE FROM public.hf_tide_prediction
                WHERE time_series_id = %s
                  AND epoch_id = %s
                  AND "time" >= %s
                  AND "time" <= %s
                """,
                (
                    int(plan.time_series_id),
                    int(plan.epoch_id),
                    pd.Timestamp(plan.delete_start).to_pydatetime(),
                    pd.Timestamp(plan.delete_end).to_pydatetime(),
                ),
            )
            rows_deleted += int(cur.rowcount)

    return rows_deleted


def _copy_hf_prediction_chunk(
    cursor,
    chunk: pd.DataFrame,
) -> int:
    if chunk.empty:
        return 0

    buffer = StringIO()
    chunk.loc[
        :, ["time", "value", "epoch_id", "resolution_id", "time_series_id"]
    ].to_csv(
        buffer,
        index=False,
        header=False,
        date_format="%Y-%m-%d %H:%M:%S",
    )
    buffer.seek(0)

    cursor.copy_expert(
        """
        COPY public.hf_tide_prediction (
            "time",
            value,
            epoch_id,
            resolution_id,
            time_series_id
        )
        FROM STDIN WITH (FORMAT CSV)
        """,
        buffer,
    )
    return int(len(chunk))


def _insert_hf_tide_prediction_plans(
    conn,
    plans: list[HfTidePredictionPlan],
) -> int:
    """Stream HF rows to TimescaleDB without materializing the full window."""

    rows_written = 0

    with conn.cursor() as cur:
        for plan in plans:
            if plan.insert_start is None or plan.insert_end is None:
                continue

            if plan.interval is None or plan.resolution_id is None:
                raise RuntimeError(
                    f"{plan.epoch_name}: HF insert window exists but the target "
                    "resolution was not resolved."
                )

            for chunk in iter_hf_prediction_chunks(
                plan.hourly_frame,
                start=plan.insert_start,
                end=plan.insert_end,
                interval=plan.interval,
                chunk_days=int(DATABASE_POLICY.hf_prediction_chunk_days),
                mm_to_meters=float(
                    DATABASE_POLICY.prediction_value_mm_to_database_meters
                ),
                value_decimal_places=int(
                    DATABASE_POLICY.prediction_value_decimal_places
                ),
            ):
                chunk["epoch_id"] = int(plan.epoch_id)
                chunk["resolution_id"] = int(plan.resolution_id)
                chunk["time_series_id"] = int(plan.time_series_id)
                rows_written += _copy_hf_prediction_chunk(cur, chunk)

    return rows_written


def _sync_record_hf_tide_predictions_to_database(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
    prediction_window: PredictionDbWindow | None = None,
) -> HfTidePredictionSyncResult | None:
    """Resolve, log, and optionally write first-class HF prediction products."""

    if (
        not DATABASE_POLICY.log_hf_tide_prediction_plan
        and not DATABASE_POLICY.write_hf_tide_predictions
    ):
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: database hf_tide_prediction sync skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        if DATABASE_POLICY.write_hf_tide_predictions:
            raise RuntimeError(
                f"{record['record_id']}: write_hf_tide_predictions=True requires "
                "populated epoch_sync.epoch_id_by_name. Enable write_epochs=True "
                "first."
            )

        notes = ["No epoch IDs available."]
        _log_hf_tide_prediction_db_plan(
            record,
            resolution_target=None,
            plans=[],
            notes=notes,
        )
        return HfTidePredictionSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            resolution_id=None,
            temporal_resolution_code=None,
            rows_planned=0,
            rows_deleted=0,
            rows_written=0,
            rows_skipped=0,
            write_hf_tide_predictions=bool(
                DATABASE_POLICY.write_hf_tide_predictions
            ),
            notes=notes,
        )

    if prediction_window is None:
        prediction_window = _resolve_record_prediction_db_window(
            record=record,
            epoch_sync=epoch_sync,
        )

    if prediction_window is None:
        raise RuntimeError(
            f"{record['record_id']}: could not resolve prediction DB window."
        )

    # Build the overlap-only plan first. This lets an old RQ product with no
    # 2016-2035 overlap reconcile stale rows without requiring that historical
    # time_series_id to have HF observations or a resolvable HF resolution.
    plans, notes = _build_hf_tide_prediction_plans(
        record=record,
        epoch_sync=epoch_sync,
        prediction_window=prediction_window,
        resolution_target=None,
    )
    has_insert_overlap = any(
        plan.insert_start is not None and plan.insert_end is not None
        for plan in plans
    )

    resolution_target: HfResolutionTarget | None = None
    rows_deleted = 0
    rows_written = 0

    needs_connection = bool(
        has_insert_overlap or DATABASE_POLICY.write_hf_tide_predictions
    )

    if needs_connection:
        with _tsdb_connection() as (conn, _ts_processor):
            if has_insert_overlap:
                resolution_target = _query_hf_resolution_target(
                    conn,
                    time_series_id=epoch_sync.time_series_id,
                )

                if resolution_target is None:
                    missing_resolution_note = (
                        "HF prediction overlap exists, but no rows were found in "
                        "public.hf_time_series_data for this exact time_series_id, "
                        "so no target minute resolution could be resolved. HF "
                        "synchronization was skipped and existing HF prediction "
                        "rows were preserved."
                    )
                    notes.append(missing_resolution_note)

                    _log_hf_tide_prediction_db_plan(
                        record,
                        resolution_target=None,
                        plans=plans,
                        notes=notes,
                    )

                    if (
                        DATABASE_POLICY.write_hf_tide_predictions
                        and DATABASE_POLICY.reconciliation_mode == "strict"
                    ):
                        raise RuntimeError(
                            f"{record['record_id']}: "
                            "write_hf_tide_predictions=True and an HF product "
                            "overlap exists, but no target resolution exists in "
                            "public.hf_time_series_data."
                        )

                    if DATABASE_POLICY.write_hf_tide_predictions:
                        log(
                            f"{record['record_id']}: database hf_tide_prediction "
                            "sync skipped in warn mode | no target resolution "
                            "exists for the exact time_series_id; existing HF "
                            "prediction rows were preserved"
                        )

                    return HfTidePredictionSyncResult(
                        record_id=str(record["record_id"]),
                        station_kind=str(record["station_kind"]),
                        time_series_id=int(epoch_sync.time_series_id),
                        id_from_source=str(epoch_sync.id_from_source),
                        input_basis_code=str(epoch_sync.input_basis_code),
                        input_basis_id=int(epoch_sync.input_basis_id),
                        resolution_id=None,
                        temporal_resolution_code=None,
                        rows_planned=0,
                        rows_deleted=0,
                        rows_written=0,
                        rows_skipped=0,
                        write_hf_tide_predictions=bool(
                            DATABASE_POLICY.write_hf_tide_predictions
                        ),
                        notes=notes,
                    )

                else:
                    # Rebuild with the resolved interval so row counts and insert
                    # metadata are exact. The underlying hourly product/window
                    # selection is unchanged.
                    plans, notes = _build_hf_tide_prediction_plans(
                        record=record,
                        epoch_sync=epoch_sync,
                        prediction_window=prediction_window,
                        resolution_target=resolution_target,
                    )

            _log_hf_tide_prediction_db_plan(
                record,
                resolution_target=resolution_target,
                plans=plans,
                notes=notes,
            )

            if DATABASE_POLICY.write_hf_tide_predictions:
                # Keep the transaction boundary at one epoch-specific HF product.
                # A full 1-minute 2016-2035 product is more than ten million rows;
                # committing each independently avoids multiplying that transaction
                # size when save_predictions_for_all_epochs=True. Reruns remain
                # idempotent because each product is fully replaced.
                for plan in plans:
                    try:
                        plan_rows_deleted = _delete_hf_tide_prediction_windows(
                            conn,
                            [plan],
                        )
                        plan_rows_written = _insert_hf_tide_prediction_plans(
                            conn,
                            [plan],
                        )
                        if plan_rows_written != plan.rows_planned:
                            raise RuntimeError(
                                f"{record['record_id']} {plan.epoch_name}: HF write "
                                f"count mismatch: expected={plan.rows_planned}, "
                                f"written={plan_rows_written}."
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise

                    rows_deleted += int(plan_rows_deleted)
                    rows_written += int(plan_rows_written)
                    log(
                        f"{record['record_id']} {plan.epoch_name}: database "
                        "hf_tide_prediction product complete | "
                        f"rows_deleted={plan_rows_deleted} | "
                        f"rows_written={plan_rows_written}"
                    )

                log(
                    f"{record['record_id']}: database hf_tide_prediction sync complete | "
                    f"rows_deleted={rows_deleted} | rows_written={rows_written}"
                )
    else:
        _log_hf_tide_prediction_db_plan(
            record,
            resolution_target=None,
            plans=plans,
            notes=notes,
        )

    return HfTidePredictionSyncResult(
        record_id=str(record["record_id"]),
        station_kind=str(record["station_kind"]),
        time_series_id=int(epoch_sync.time_series_id),
        id_from_source=str(epoch_sync.id_from_source),
        input_basis_code=str(epoch_sync.input_basis_code),
        input_basis_id=int(epoch_sync.input_basis_id),
        resolution_id=(
            None
            if resolution_target is None
            else int(resolution_target.resolution_id)
        ),
        temporal_resolution_code=(
            None
            if resolution_target is None
            else str(resolution_target.temporal_resolution_code)
        ),
        rows_planned=int(sum(plan.rows_planned for plan in plans)),
        rows_deleted=int(rows_deleted),
        rows_written=int(rows_written),
        rows_skipped=0,
        write_hf_tide_predictions=bool(
            DATABASE_POLICY.write_hf_tide_predictions
        ),
        notes=notes,
    )

def _build_high_low_prediction_db_dataframe(
    *,
    record: dict[str, Any],
    epoch_sync: EpochSyncResult,
    prediction_window: PredictionDbWindow,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    notes: list[str] = []

    if not epoch_sync.epoch_id_by_name:
        raise RuntimeError(
            f"{record['record_id']}: cannot build high_low_prediction rows because "
            "epoch_sync.epoch_id_by_name is empty. Enable write_epochs=True "
            "before write_high_low_predictions=True."
        )

    station_kind = str(record["station_kind"]).upper()
    high_low_frames = record.get("_minute_highlow_frames") or {}
    rows: list[pd.DataFrame] = []
    delete_windows: list[dict[str, Any]] = []

    epoch_summary_by_name = {
        str(epoch_summary["epoch"]["name"]): epoch_summary
        for epoch_summary in record.get("epochs") or []
    }

    if station_kind == "RQ":
        prediction_summaries = record.get("hourly_predictions") or []

        if not prediction_summaries:
            notes.append(
                "No saved hourly prediction products available for RQ high/low DB generation."
            )

    else:
        prediction_summaries = record.get("minute_highlow_predictions") or []

        if not prediction_summaries:
            notes.append("No generated high/low prediction products for this record.")

    for prediction_summary in prediction_summaries:
        epoch_name = str(prediction_summary["epoch"])
        prediction_key = str(prediction_summary["prediction_key"])

        if epoch_name not in epoch_sync.epoch_id_by_name:
            raise RuntimeError(
                f"{record['record_id']}: high/low prediction epoch {epoch_name!r} "
                f"is missing from epoch_sync.epoch_id_by_name={epoch_sync.epoch_id_by_name}."
            )

        if epoch_name not in epoch_summary_by_name:
            raise RuntimeError(
                f"{record['record_id']}: high/low prediction epoch {epoch_name!r} "
                "is missing from record['epochs']."
            )

        generated_start = pd.Timestamp(prediction_summary["start"])
        generated_end = pd.Timestamp(prediction_summary["end"])

        allowed_start = generated_start
        allowed_end = generated_end
        delete_start = generated_start
        delete_end = generated_end

        if station_kind == "RQ":
            allowed_start, allowed_end = _allowed_prediction_window_from_db_window(
                generated_start=generated_start,
                generated_end=generated_end,
                prediction_window=prediction_window,
            )

            record_quality_short_name = (
                prediction_window.record_quality_short_name or "unknown"
            )

            if allowed_end < allowed_start:
                notes.append(
                    f"No overlap between generated high/low source window "
                    f"{generated_start} to {generated_end} and DB "
                    f"{record_quality_short_name}/"
                    f"{prediction_window.temporal_resolution_code} "
                    f"valid range {prediction_window.date_begin} to "
                    f"{prediction_window.date_end}."
                )
                continue

            # If this RQ record was ever accidentally written with the generated
            # long-future/hourly window or an operational high-low window, delete
            # the union of that generated window and the DB-authoritative RQ
            # window, then reinsert only bounded RQ rows.
            delete_start = min(generated_start, allowed_start)
            delete_end = max(generated_end, allowed_end)

            notes.append(
                f"{record_quality_short_name.upper()} high/low prediction generated "
                f"for DB write from date_range_by_time_series_quality: "
                f"{allowed_start} to {allowed_end}."
            )

            harmonics = load_harmonic_result(
                epoch_summary_by_name[epoch_name]["harmonic_artifact"]["pickle"]
            )

            try:
                frame = predict_minute_high_low(
                    harmonics,
                    start=allowed_start,
                    end=allowed_end,
                )
            finally:
                del harmonics
                gc.collect()

        else:
            # FD/current best_available high-low keeps the generated operational
            # high-low window. It is intentionally not bounded by the FD matview
            # date_end/tail while it is the current resolved best_available target.
            if prediction_key not in high_low_frames:
                raise RuntimeError(
                    f"{record['record_id']}: missing high/low prediction frame for "
                    f"prediction_key={prediction_key!r}."
                )

            frame = high_low_frames[prediction_key].copy()

        epoch_id = int(epoch_sync.epoch_id_by_name[epoch_name])

        delete_windows.append(
            {
                "time_series_id": int(epoch_sync.time_series_id),
                "epoch_id": epoch_id,
                "delete_start": delete_start,
                "delete_end": delete_end,
                "insert_start": allowed_start,
                "insert_end": allowed_end,
                "epoch_name": epoch_name,
            }
        )

        frame["time"] = pd.to_datetime(frame["time"])
        frame = frame[
            (frame["time"] >= allowed_start)
            & (frame["time"] <= allowed_end)
        ].copy()

        if frame.empty:
            notes.append(
                f"No high/low prediction rows remained after DB window filtering for "
                f"{record['record_id']} {epoch_name}."
            )
            continue

        required_columns = {"time", "height_mm", "type"}

        if not required_columns.issubset(set(frame.columns)):
            raise RuntimeError(
                f"{record['record_id']} {epoch_name}: high/low frame missing "
                f"required columns {required_columns}; columns={list(frame.columns)}."
            )

        frame = pd.DataFrame(
            {
                "time": frame["time"],
                "value": frame["height_mm"].map(_database_prediction_value_from_mm),
                "tide_type": frame["type"].astype(str),
                "epoch_id": epoch_id,
                "time_series_id": int(epoch_sync.time_series_id),
                "epoch_name": epoch_name,
            }
        )

        rows.append(frame)

    if not rows:
        return pd.DataFrame(
            columns=[
                "time",
                "value",
                "tide_type",
                "epoch_id",
                "time_series_id",
                "epoch_name",
            ]
        ), delete_windows, notes

    return pd.concat(rows, ignore_index=True), delete_windows, notes


def _log_high_low_prediction_db_plan(
    record: dict[str, Any],
    high_low_df: pd.DataFrame,
    *,
    delete_windows: list[dict[str, Any]],
    notes: list[str],
) -> None:
    log(
        f"{record['record_id']}: database high_low_prediction sync plan | "
        f"rows={len(high_low_df)} | "
        f"delete_windows={len(delete_windows)} | "
        f"value_units=meters | "
        f"write_high_low_predictions={DATABASE_POLICY.write_high_low_predictions}"
    )

    for note in notes:
        log(f"{record['record_id']}: high_low_prediction note | {note}")

    for window in delete_windows:
        log(
            f"{record['record_id']}: "
            f"{'REPLACE' if DATABASE_POLICY.write_high_low_predictions else 'WOULD REPLACE'} "
            f"high_low_prediction rows for epoch {window['epoch_name']} | "
            f"delete_window={window['delete_start']} to {window['delete_end']} | "
            f"insert_window={window.get('insert_start', window['delete_start'])} "
            f"to {window.get('insert_end', window['delete_end'])}"
        )


def _delete_high_low_prediction_windows(
    conn,
    delete_windows: list[dict[str, Any]],
) -> int:
    rows_deleted = 0

    with conn.cursor() as cur:
        for window in delete_windows:
            cur.execute(
                """
                DELETE FROM public.high_low_prediction
                WHERE time_series_id = %s
                  AND epoch_id = %s
                  AND "time" >= %s
                  AND "time" <= %s
                """,
                (
                    int(window["time_series_id"]),
                    int(window["epoch_id"]),
                    pd.Timestamp(window["delete_start"]).to_pydatetime(),
                    pd.Timestamp(window["delete_end"]).to_pydatetime(),
                ),
            )
            rows_deleted += int(cur.rowcount)

    return rows_deleted


def _insert_high_low_prediction_rows(
    conn,
    high_low_df: pd.DataFrame,
) -> int:
    if high_low_df.empty:
        return 0

    batch_size = _prediction_insert_batch_size()

    insert_sql = """
        INSERT INTO public.high_low_prediction (
            "time",
            value,
            tide_type,
            epoch_id,
            time_series_id
        )
        VALUES %s
        ON CONFLICT (time_series_id, epoch_id, "time")
        DO UPDATE SET
            value = EXCLUDED.value,
            tide_type = EXCLUDED.tide_type
    """

    rows_written = 0
    insert_columns = [
        "time",
        "value",
        "tide_type",
        "epoch_id",
        "time_series_id",
    ]

    with conn.cursor() as cur:
        for batch_df in _iter_dataframe_batches(high_low_df, batch_size):
            rows = [
                (
                    pd.Timestamp(time_value).to_pydatetime(),
                    float(value),
                    str(tide_type),
                    int(epoch_id),
                    int(time_series_id),
                )
                for (
                    time_value,
                    value,
                    tide_type,
                    epoch_id,
                    time_series_id,
                ) in batch_df.loc[:, insert_columns].itertuples(index=False, name=None)
            ]

            if not rows:
                continue

            execute_values(
                cur,
                insert_sql,
                rows,
                page_size=batch_size,
            )

            rows_written += len(rows)

    return rows_written


def _sync_record_high_low_predictions_to_database(
    record: dict[str, Any],
    *,
    epoch_sync: EpochSyncResult | None,
    prediction_window: PredictionDbWindow | None = None,
) -> HighLowPredictionSyncResult | None:
    """Resolve, log, and optionally write high_low_prediction rows.

    This function intentionally consumes EpochSyncResult. It must not perform
    independent station/version resolution.
    """

    if (
        not DATABASE_POLICY.log_high_low_prediction_plan
        and not DATABASE_POLICY.write_high_low_predictions
    ):
        return None

    if epoch_sync is None:
        log(
            f"{record['record_id']}: database high_low_prediction sync skipped | "
            "no epoch sync result available"
        )
        return None

    if not epoch_sync.epoch_id_by_name:
        if DATABASE_POLICY.write_high_low_predictions:
            raise RuntimeError(
                f"{record['record_id']}: write_high_low_predictions=True requires "
                "populated epoch_sync.epoch_id_by_name. Enable write_epochs=True first."
            )

        log(
            f"{record['record_id']}: database high_low_prediction sync skipped | "
            "no epoch IDs available in dry-run/no-write mode"
        )

        return HighLowPredictionSyncResult(
            record_id=str(record["record_id"]),
            station_kind=str(record["station_kind"]),
            time_series_id=int(epoch_sync.time_series_id),
            id_from_source=str(epoch_sync.id_from_source),
            input_basis_code=str(epoch_sync.input_basis_code),
            input_basis_id=int(epoch_sync.input_basis_id),
            rows_planned=0,
            rows_deleted=0,
            rows_written=0,
            rows_skipped=0,
            write_high_low_predictions=bool(DATABASE_POLICY.write_high_low_predictions),
            notes=["No epoch IDs available."],
        )

    if prediction_window is None:
        prediction_window = _resolve_record_prediction_db_window(
            record=record,
            epoch_sync=epoch_sync,
        )

    if prediction_window is None:
        raise RuntimeError(
            f"{record['record_id']}: could not resolve prediction DB window."
        )

    high_low_df, delete_windows, notes = _build_high_low_prediction_db_dataframe(
        record=record,
        epoch_sync=epoch_sync,
        prediction_window=prediction_window,
    )

    _log_high_low_prediction_db_plan(
        record,
        high_low_df,
        delete_windows=delete_windows,
        notes=notes,
    )

    rows_deleted = 0
    rows_written = 0

    if DATABASE_POLICY.write_high_low_predictions:
        with _tsdb_connection() as (conn, _ts_processor):
            try:
                rows_deleted = _delete_high_low_prediction_windows(
                    conn,
                    delete_windows,
                )
                rows_written = _insert_high_low_prediction_rows(
                    conn,
                    high_low_df,
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

        log(
            f"{record['record_id']}: database high_low_prediction sync complete | "
            f"rows_deleted={rows_deleted} | rows_written={rows_written}"
        )

    return HighLowPredictionSyncResult(
        record_id=str(record["record_id"]),
        station_kind=str(record["station_kind"]),
        time_series_id=int(epoch_sync.time_series_id),
        id_from_source=str(epoch_sync.id_from_source),
        input_basis_code=str(epoch_sync.input_basis_code),
        input_basis_id=int(epoch_sync.input_basis_id),
        rows_planned=int(len(high_low_df)),
        rows_deleted=int(rows_deleted),
        rows_written=int(rows_written),
        rows_skipped=0,
        write_high_low_predictions=bool(DATABASE_POLICY.write_high_low_predictions),
        notes=notes,
    )


def _query_time_series_id_by_id_from_source(conn, id_from_source: str) -> int:
    row = _fetchone_dict(
        conn,
        """
        SELECT id
        FROM public.time_series
        WHERE upper(id_from_source) = upper(%s)
        """,
        (str(id_from_source),),
    )

    if row is None:
        raise RuntimeError(
            f"No time_series row found for id_from_source={id_from_source!r}."
        )

    return int(row["id"])


def _run_prediction_cutover_cleanup() -> None:
    """Explicit best_available D->E cleanup.

    This never guesses the cutover boundary. It only executes configured cleanup
    entries in DATABASE_POLICY.prediction_cutover_cleanups.

    Each cleanup deletes old best_available prediction rows at/after cutover
    for the old time_series row. It preserves epochs, datums, constituents, and
    historical/bounded predictions before the cutover.
    """

    cleanups = tuple(DATABASE_POLICY.prediction_cutover_cleanups)

    if not cleanups:
        return

    if (
        not DATABASE_POLICY.log_prediction_cutover_cleanup_plan
        and not DATABASE_POLICY.write_prediction_cutover_cleanup
    ):
        return

    CommonUtils, TSDataProcessor = _load_database_tools()
    env_utils = CommonUtils()
    ts_processor = TSDataProcessor()
    conn = env_utils.connect_2_tsdb()

    try:
        best_available_input_basis_id = ts_processor.query_prediction_input_basis_id_tsdb(
            conn,
            DATABASE_POLICY.fd_input_basis_code,
        )

        for old_id_from_source, new_id_from_source, cutover_time_raw in cleanups:
            old_time_series_id = _query_time_series_id_by_id_from_source(
                conn,
                old_id_from_source,
            )
            new_time_series_id = _query_time_series_id_by_id_from_source(
                conn,
                new_id_from_source,
            )
            cutover_time = pd.Timestamp(cutover_time_raw)

            log(
                "prediction cutover cleanup plan | "
                f"old={old_id_from_source} time_series_id={old_time_series_id} | "
                f"new={new_id_from_source} time_series_id={new_time_series_id} | "
                f"cutover_time={cutover_time} | "
                f"write_cleanup={DATABASE_POLICY.write_prediction_cutover_cleanup}"
            )

            if not DATABASE_POLICY.write_prediction_cutover_cleanup:
                continue

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM public.tide_prediction p
                        USING public.epoch e
                        WHERE p.epoch_id = e.id
                          AND p.time_series_id = %s
                          AND e.time_series_id = %s
                          AND e.input_basis_id = %s
                          AND p."time" >= %s
                        """,
                        (
                            int(old_time_series_id),
                            int(old_time_series_id),
                            int(best_available_input_basis_id),
                            cutover_time.to_pydatetime(),
                        ),
                    )
                    tide_rows_deleted = int(cur.rowcount)

                    cur.execute(
                        """
                        DELETE FROM public.hf_tide_prediction p
                        USING public.epoch e
                        WHERE p.epoch_id = e.id
                          AND p.time_series_id = %s
                          AND e.time_series_id = %s
                          AND e.input_basis_id = %s
                          AND p."time" >= %s
                        """,
                        (
                            int(old_time_series_id),
                            int(old_time_series_id),
                            int(best_available_input_basis_id),
                            cutover_time.to_pydatetime(),
                        ),
                    )
                    hf_tide_rows_deleted = int(cur.rowcount)

                    cur.execute(
                        """
                        DELETE FROM public.high_low_prediction p
                        USING public.epoch e
                        WHERE p.epoch_id = e.id
                          AND p.time_series_id = %s
                          AND e.time_series_id = %s
                          AND e.input_basis_id = %s
                          AND p."time" >= %s
                        """,
                        (
                            int(old_time_series_id),
                            int(old_time_series_id),
                            int(best_available_input_basis_id),
                            cutover_time.to_pydatetime(),
                        ),
                    )
                    high_low_rows_deleted = int(cur.rowcount)

                conn.commit()

            except Exception:
                conn.rollback()
                raise

            log(
                "prediction cutover cleanup complete | "
                f"old={old_id_from_source} | "
                f"tide_prediction_rows_deleted={tide_rows_deleted} | "
                f"hf_tide_prediction_rows_deleted={hf_tide_rows_deleted} | "
                f"high_low_prediction_rows_deleted={high_low_rows_deleted}"
            )

    finally:
        conn.close()


def _query_best_available_date_range_rows_for_current_target(
    conn,
    *,
    current_time_series_id: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Return FD/hourly date-range rows for the current target's station/priority."""

    notes: list[str] = []

    current_row = _fetchone_dict(
        conn,
        """
        SELECT
          r.time_series_id,
          r.id_from_source,
          r.station_id,
          r.record_id,
          r.priority,
          r.date_begin,
          r.date_end,
          r.last_update
        FROM public.date_range_by_time_series_quality r
        JOIN public.record_quality q
          ON q.id = r.quality_id
        JOIN public.temporal_resolution tr
          ON tr.id = r.resolution_id
        WHERE r.time_series_id = %s
          AND lower(q.short_name) = lower(%s)
          AND tr.resolution = %s
        ORDER BY r.date_begin
        LIMIT 1
        """,
        (
            int(current_time_series_id),
            DATABASE_POLICY.fd_record_quality_short_name,
            DATABASE_POLICY.hourly_temporal_resolution_code,
        ),
    )

    if current_row is None:
        notes.append(
            "Current FD target was not found in "
            "date_range_by_time_series_quality for fd/hourly; "
            "automatic cleanup skipped."
        )
        return [], None, notes

    current_row["date_begin"] = _to_database_window_timestamp(current_row["date_begin"])
    current_row["date_end"] = _to_database_window_timestamp(current_row["date_end"])
    current_row["last_update"] = _to_database_window_timestamp(current_row["last_update"])

    rows = _fetchall_dicts(
        conn,
        """
        SELECT
          r.time_series_id,
          r.id_from_source,
          r.station_id,
          r.record_id,
          r.priority,
          r.date_begin,
          r.date_end,
          r.last_update
        FROM public.date_range_by_time_series_quality r
        JOIN public.record_quality q
          ON q.id = r.quality_id
        JOIN public.temporal_resolution tr
          ON tr.id = r.resolution_id
        WHERE r.station_id = %s
          AND r.priority = %s
          AND lower(q.short_name) = lower(%s)
          AND tr.resolution = %s
        ORDER BY r.date_begin, r.date_end, r.time_series_id
        """,
        (
            current_row["station_id"],
            current_row["priority"],
            DATABASE_POLICY.fd_record_quality_short_name,
            DATABASE_POLICY.hourly_temporal_resolution_code,
        ),
    )

    for row in rows:
        row["date_begin"] = _to_database_window_timestamp(row["date_begin"])
        row["date_end"] = _to_database_window_timestamp(row["date_end"])
        row["last_update"] = _to_database_window_timestamp(row["last_update"])

    return rows, current_row, notes


def _build_best_available_prediction_cleanup_actions(
    *,
    current_time_series_id: int,
    current_id_from_source: str,
    range_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build cleanup actions for FD targets superseded by the current target."""

    notes: list[str] = []

    if not range_rows:
        return [], notes

    current_indexes = [
        idx
        for idx, row in enumerate(range_rows)
        if int(row["time_series_id"]) == int(current_time_series_id)
    ]

    if not current_indexes:
        notes.append(
            f"Current target {current_id_from_source} / time_series_id="
            f"{current_time_series_id} was not found among FD/hourly range rows; "
            "automatic cleanup skipped."
        )
        return [], notes

    if len(current_indexes) > 1:
        notes.append(
            f"Current target {current_id_from_source} appeared multiple times in "
            "FD/hourly range rows; automatic cleanup skipped."
        )
        return [], notes

    current_index = current_indexes[0]

    if current_index < len(range_rows) - 1:
        future_rows = range_rows[current_index + 1 :]
        notes.append(
            "FD/hourly rows exist after the currently resolved target. "
            "They will not be treated as current until the FD resolver maps the "
            "unversioned source to them: "
            + ", ".join(str(row["id_from_source"]) for row in future_rows)
        )

    actions: list[dict[str, Any]] = []

    for row in range_rows[:current_index]:
        valid_start = pd.Timestamp(row["date_begin"])
        valid_end = pd.Timestamp(row["date_end"])

        if valid_end < valid_start:
            raise RuntimeError(
                "Invalid FD date range from date_range_by_time_series_quality: "
                f"id_from_source={row['id_from_source']}, "
                f"date_begin={valid_start}, date_end={valid_end}."
            )

        actions.append(
            {
                "time_series_id": int(row["time_series_id"]),
                "id_from_source": str(row["id_from_source"]),
                "valid_start": valid_start,
                "valid_end": valid_end,
                "date_range_last_update": pd.Timestamp(row["last_update"]),
            }
        )

    return actions, notes


def _delete_predictions_outside_best_available_windows(
    conn,
    *,
    actions: list[dict[str, Any]],
    best_available_input_basis_id: int,
    hourly_resolution_id: int,
) -> tuple[int, int, int]:
    """Delete stale superseded best_available prediction rows.

    For superseded BA targets, keep only rows attached to the primary prediction
    basis epoch and only inside the DB-authoritative FD valid window.
    """

    tide_prediction_rows_deleted = 0
    hf_tide_prediction_rows_deleted = 0
    high_low_prediction_rows_deleted = 0

    with conn.cursor() as cur:
        for action in actions:
            old_time_series_id = int(action["time_series_id"])
            valid_start = pd.Timestamp(action["valid_start"]).to_pydatetime()
            valid_end = pd.Timestamp(action["valid_end"]).to_pydatetime()

            # 1. Delete superseded BA hourly tide predictions outside the
            # DB-authoritative FD valid window.
            cur.execute(
                """
                DELETE FROM public.tide_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND p.resolution_id = %s
                  AND (
                    p."time" < %s
                    OR p."time" > %s
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                    int(hourly_resolution_id),
                    valid_start,
                    valid_end,
                ),
            )
            tide_prediction_rows_deleted += int(cur.rowcount)

            # 2. Delete superseded BA hourly tide predictions attached to stale
            # non-primary / non-prediction-basis epochs, even if they fall inside
            # the valid FD window.
            cur.execute(
                """
                DELETE FROM public.tide_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND p.resolution_id = %s
                  AND NOT (
                    COALESCE(e.primary, false) = true
                    AND COALESCE(e.is_prediction_basis, false) = true
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                    int(hourly_resolution_id),
                ),
            )
            tide_prediction_rows_deleted += int(cur.rowcount)

            # 3. Delete superseded BA HF tide predictions outside the
            # DB-authoritative FD valid window.
            cur.execute(
                """
                DELETE FROM public.hf_tide_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND (
                    p."time" < %s
                    OR p."time" > %s
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                    valid_start,
                    valid_end,
                ),
            )
            hf_tide_prediction_rows_deleted += int(cur.rowcount)

            # 4. Delete superseded BA HF predictions attached to stale
            # non-primary / non-prediction-basis epochs.
            cur.execute(
                """
                DELETE FROM public.hf_tide_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND NOT (
                    COALESCE(e.primary, false) = true
                    AND COALESCE(e.is_prediction_basis, false) = true
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                ),
            )
            hf_tide_prediction_rows_deleted += int(cur.rowcount)

            # 5. Delete superseded BA high/low predictions outside the
            # DB-authoritative FD valid window.
            cur.execute(
                """
                DELETE FROM public.high_low_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND (
                    p."time" < %s
                    OR p."time" > %s
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                    valid_start,
                    valid_end,
                ),
            )
            high_low_prediction_rows_deleted += int(cur.rowcount)

            # 6. Delete superseded BA high/low predictions attached to stale
            # non-primary / non-prediction-basis epochs, even if they fall inside
            # the valid FD window.
            cur.execute(
                """
                DELETE FROM public.high_low_prediction p
                USING public.epoch e
                WHERE p.epoch_id = e.id
                  AND p.time_series_id = %s
                  AND e.time_series_id = %s
                  AND e.input_basis_id = %s
                  AND NOT (
                    COALESCE(e.primary, false) = true
                    AND COALESCE(e.is_prediction_basis, false) = true
                  )
                """,
                (
                    old_time_series_id,
                    old_time_series_id,
                    int(best_available_input_basis_id),
                ),
            )
            high_low_prediction_rows_deleted += int(cur.rowcount)

    return (
        tide_prediction_rows_deleted,
        hf_tide_prediction_rows_deleted,
        high_low_prediction_rows_deleted,
    )


def _run_auto_best_available_prediction_cleanup(
    *,
    fd_epoch_sync: EpochSyncResult | None,
) -> PredictionAutoCleanupResult | None:
    """Automatically trim superseded FD/best_available predictions."""

    if (
        not DATABASE_POLICY.auto_cleanup_superseded_best_available_predictions
        and not DATABASE_POLICY.write_prediction_auto_cleanup
    ):
        return None

    if fd_epoch_sync is None:
        log("prediction auto cleanup skipped | no FD epoch sync result available")
        return None

    if fd_epoch_sync.input_basis_code != DATABASE_POLICY.fd_input_basis_code:
        log(
            "prediction auto cleanup skipped | FD epoch sync result did not use "
            f"input_basis={DATABASE_POLICY.fd_input_basis_code}"
        )
        return None

    with _tsdb_connection() as (conn, ts_processor):
        best_available_input_basis_id = ts_processor.query_prediction_input_basis_id_tsdb(
            conn,
            DATABASE_POLICY.fd_input_basis_code,
        )
        hourly_resolution_id = _query_temporal_resolution_id(
            conn,
            DATABASE_POLICY.hourly_temporal_resolution_code,
        )

        range_rows, current_range_row, notes = (
            _query_best_available_date_range_rows_for_current_target(
                conn,
                current_time_series_id=fd_epoch_sync.time_series_id,
            )
        )

        if current_range_row is None:
            return PredictionAutoCleanupResult(
                current_time_series_id=int(fd_epoch_sync.time_series_id),
                current_id_from_source=str(fd_epoch_sync.id_from_source),
                fd_quality_short_name=DATABASE_POLICY.fd_record_quality_short_name,
                temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
                actions=[],
                tide_prediction_rows_deleted=0,
                hf_tide_prediction_rows_deleted=0,
                high_low_prediction_rows_deleted=0,
                write_cleanup=bool(DATABASE_POLICY.write_prediction_auto_cleanup),
                notes=notes,
            )

        actions, action_notes = _build_best_available_prediction_cleanup_actions(
            current_time_series_id=fd_epoch_sync.time_series_id,
            current_id_from_source=fd_epoch_sync.id_from_source,
            range_rows=range_rows,
        )
        notes.extend(action_notes)

        if DATABASE_POLICY.log_prediction_auto_cleanup_plan:
            log(
                "prediction auto cleanup plan | "
                f"current={fd_epoch_sync.id_from_source} "
                f"time_series_id={fd_epoch_sync.time_series_id} | "
                f"actions={len(actions)} | "
                f"write_cleanup={DATABASE_POLICY.write_prediction_auto_cleanup}"
            )

            for note in notes:
                log(f"prediction auto cleanup note | {note}")

            for action in actions:
                log(
                    "prediction auto cleanup action | "
                    f"id_from_source={action['id_from_source']} | "
                    f"time_series_id={action['time_series_id']} | "
                    f"keep_window={action['valid_start']} to {action['valid_end']} | "
                    f"date_range_last_update={action['date_range_last_update']}"
                )

        tide_rows_deleted = 0
        hf_tide_rows_deleted = 0
        high_low_rows_deleted = 0

        if DATABASE_POLICY.write_prediction_auto_cleanup and actions:
            try:
                tide_rows_deleted, hf_tide_rows_deleted, high_low_rows_deleted = (
                    _delete_predictions_outside_best_available_windows(
                        conn,
                        actions=actions,
                        best_available_input_basis_id=best_available_input_basis_id,
                        hourly_resolution_id=hourly_resolution_id,
                    )
                )
                conn.commit()

            except Exception:
                conn.rollback()
                raise

            log(
                "prediction auto cleanup complete | "
                f"tide_prediction_rows_deleted={tide_rows_deleted} | "
                f"hf_tide_prediction_rows_deleted={hf_tide_rows_deleted} | "
                f"high_low_prediction_rows_deleted={high_low_rows_deleted}"
            )

        return PredictionAutoCleanupResult(
            current_time_series_id=int(fd_epoch_sync.time_series_id),
            current_id_from_source=str(fd_epoch_sync.id_from_source),
            fd_quality_short_name=DATABASE_POLICY.fd_record_quality_short_name,
            temporal_resolution_code=DATABASE_POLICY.hourly_temporal_resolution_code,
            actions=actions,
            tide_prediction_rows_deleted=int(tide_rows_deleted),
            hf_tide_prediction_rows_deleted=int(hf_tide_rows_deleted),
            high_low_prediction_rows_deleted=int(high_low_rows_deleted),
            write_cleanup=bool(DATABASE_POLICY.write_prediction_auto_cleanup),
            notes=notes,
        )


def _resolve_station_ids(value: str) -> list[str]:
    value = str(value).strip()
    if value.lower() == "all":
        return sorted(fetch_station_metadata_index().keys())
    return [str(int(part.strip())).zfill(3) for part in value.split(",") if part.strip()]


def _compute_residual_metrics(merged: pd.DataFrame) -> dict:
    residual = (
        pd.to_numeric(merged["sea_level"], errors="coerce")
        - pd.to_numeric(merged["prediction_mm"], errors="coerce")
    )
    residual = residual[np.isfinite(residual)]

    if residual.empty:
        return {
            "residual_rows": 0,
            "epoch_rmse_mm": None,
            "epoch_residual_mean_mm": None,
            "epoch_residual_std_mm": None,
            "epoch_residual_abs_p95_mm": None,
        }

    return {
        "residual_rows": int(len(residual)),
        "epoch_rmse_mm": float(np.sqrt(np.mean(np.square(residual)))),
        "epoch_residual_mean_mm": float(np.mean(residual)),
        "epoch_residual_std_mm": float(np.std(residual)),
        "epoch_residual_abs_p95_mm": float(np.nanpercentile(np.abs(residual), 95)),
    }


def _plot_hourly_comparison(plot_path: Path, merged: pd.DataFrame, title: str) -> dict:
    plot_df = merged.head(24 * 31).copy()

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(plot_df["time"], plot_df["sea_level"], label="Observed", linewidth=1.0)
    ax.plot(plot_df["time"], plot_df["prediction_mm"], label="Predicted", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("Sea Level (mm, station zero)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    residual = plot_df["sea_level"] - plot_df["prediction_mm"]

    return {
        "comparison_plot": str(plot_path),
        "plot_window_rows": int(len(plot_df)),
        "plot_window_rmse_mm": float(np.sqrt(np.mean(np.square(residual)))) if len(plot_df) else None,
    }


def _plot_datums(plot_path: Path, series: pd.DataFrame, datum, switch_levels, title: str) -> str:
    plot_df = series.dropna(subset=["sea_level"]).head(24 * 31).copy()
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(plot_df["time"], plot_df["sea_level"], color="0.25", linewidth=0.9, label="Observed hourly sea level")
    datum_lines = {
        "MHHW": datum.MHHW,
        "MHW": datum.MHW,
        "MSL": datum.MSL,
        "MLW": datum.MLW,
        "MLLW": datum.MLLW,
    }
    colors = {
        "MHHW": "tab:blue",
        "MHW": "tab:cyan",
        "MSL": "tab:green",
        "MLW": "tab:orange",
        "MLLW": "tab:red",
    }
    if switch_levels is not None:
        if switch_levels.LEV is not None:
            datum_lines["LEV"] = float(switch_levels.LEV)
            colors["LEV"] = "tab:purple"
        if switch_levels.LEVB is not None:
            datum_lines["LEVB"] = float(switch_levels.LEVB)
            colors["LEVB"] = "tab:brown"
    for name, value in datum_lines.items():
        ax.axhline(value, color=colors[name], linestyle="--", linewidth=1.1, label=f"{name} = {value:.1f} mm")
    ax.set_title(title)
    ax.set_ylabel("Sea Level (mm, station zero)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return str(plot_path)


def _plot_residuals(plot_path: Path, merged: pd.DataFrame, title: str) -> str:
    plot_df = merged.copy()
    residual = plot_df["sea_level"] - plot_df["prediction_mm"]

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(plot_df["time"], residual, color="black", linewidth=0.6)
    ax.axhline(0.0, color="tab:red", linestyle="--", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("Observed - Predicted (mm)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    return str(plot_path)


def _plot_fd_high_low(plot_path: Path, high_low: pd.DataFrame, title: str) -> str | None:
    if high_low.empty:
        return None
    plot_df = high_low.head(120).copy()
    colors = plot_df["type"].map({"H": "tab:blue", "L": "tab:orange"}).to_numpy()
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.scatter(plot_df["time"], plot_df["height_mm"], c=colors, s=18)
    ax.plot(plot_df["time"], plot_df["height_mm"], color="0.5", linewidth=0.8, alpha=0.7)
    ax.set_title(title)
    ax.set_ylabel("Predicted Tide (mm relative to station zero)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return str(plot_path)


def _run_record(
    station_id: str,
    station_kind: str,
    outdir: Path,
    version: str | None = None,
) -> dict:
    requested_record = station_id if station_kind == "FD" else f"{station_id}{str(version).lower()}"
    log(f"Starting {station_kind} record {requested_record}")
    station_meta = get_station_metadata(station_id)
    switch_levels = get_station_switch_levels(station_id)
    latitude = station_meta.latitude
    if station_kind == "FD":
        log(f"Fetching FD hourly data for station {station_id}")
        raw = fetch_fd_hourly(station_id)
        record_id = station_id
    else:
        assert version is not None
        log(f"Fetching RQ hourly data for station {station_id} version {version}")
        raw = fetch_rq_hourly(station_id, version)
        record_id = f"{station_id}{version.lower()}"

    station_name = str(raw["station_name"].dropna().iloc[0])
    df = clean_hourly_dataframe(raw[["time", "sea_level"]])
    valid_rows = int(df["sea_level"].notna().sum())
    log(f"{record_id}: loaded {len(df)} hourly rows ({valid_rows} valid)")
    observation_start = pd.Timestamp(df["time"].min())
    observation_end = pd.Timestamp(df["time"].max())

    epochs = select_epochs(df)

    if not epochs:
        valid_times = df.loc[df["sea_level"].notna(), "time"]
        valid_start = (
            None
            if valid_times.empty
            else pd.Timestamp(valid_times.min())
        )
        valid_end = (
            None
            if valid_times.empty
            else pd.Timestamp(valid_times.max())
        )

        raise NoQualifyingEpochError(
            f"No qualifying epochs found for {record_id}: "
            f"total_rows={len(df)}, valid_rows={valid_rows}, "
            f"observation_window={observation_start} to {observation_end}, "
            f"valid_window={valid_start} to {valid_end}, "
            f"minimum_completion_fraction="
            f"{EPOCH_POLICY.min_completion_fraction}, "
            f"minimum_recent_months={EPOCH_POLICY.min_recent_months}."
        )

    log(f"{record_id}: selected epochs: {', '.join(ep.name for ep in epochs)}")

    datum_by_epoch = {}
    harmonics_by_epoch = {}
    hourly_predictions = {}
    harmonic_artifacts = {}
    minute_highlow_by_epoch = {}
    hourly_prediction_summaries = []
    minute_highlow_prediction_summaries = []
    epoch_summaries = []
    epoch_summary_by_name = {}
    prediction_plan = build_prediction_save_plan(df, epochs, station_kind, station_id=station_id, version=version)
    log(
        f"{record_id}: prediction basis {prediction_plan.basis_epoch}; "
        f"hourly {prediction_plan.hourly_start} to {prediction_plan.hourly_end}; "
        f"minute high/low saved={prediction_plan.save_minute_high_low}"
    )
    plot_dir = outdir / "plots" / record_id
    plot_dir.mkdir(parents=True, exist_ok=True)

    for ep in epochs:
        log(f"{record_id} {ep.name}: fitting harmonics and calculating datums")
        sub = df[(df["time"] >= ep.start) & (df["time"] <= ep.end)].copy()
        fit_observed = prepare_harmonic_fit_dataframe(sub)

        if len(fit_observed) < 24 * 30:
            raise ValueError(
                f"{record_id} {ep.name}: Need at least ~30 days of valid hourly "
                f"observations for harmonic analysis after cleaning; found "
                f"{len(fit_observed)} rows."
            )

        fit_begin = pd.Timestamp(fit_observed["time"].min())
        fit_end = pd.Timestamp(fit_observed["time"].max())

        fitted_harmonics = fit_harmonics(fit_observed, latitude=latitude)
        harmonic_path = outdir / "harmonics" / record_id / f"{ep.name}_harmonics.pkl"
        harmonic_artifacts[ep.name] = save_harmonic_result(
            fitted_harmonics,
            str(harmonic_path),
            metadata={
                "station_id": record_id,
                "station_name": station_name,
                "station_kind": station_kind,
                "epoch_name": ep.name,
                "epoch_start": str(ep.start),
                "epoch_end": str(ep.end),
                "fit_begin": str(fit_begin),
                "fit_end": str(fit_end),
                "fit_observation_rows": int(len(fit_observed)),
                "latitude": float(latitude),
                "hat_lat_prediction_start": str(HAT_LAT_PREDICTION_START),
                "hat_lat_prediction_end": str(HAT_LAT_PREDICTION_END),
            },
        )
        harmonics_summary = strip_harmonic_result(fitted_harmonics)
        del fitted_harmonics
        gc.collect()

        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])
        epoch_hourly_pred = predict_from_harmonics(harmonics, ep.start, ep.end, freq="1h")
        hat_lat_pred = predict_from_harmonics(
            harmonics,
            HAT_LAT_PREDICTION_START,
            HAT_LAT_PREDICTION_END,
            freq="1h",
        )
        datum = compute_datums(
            sub,
            epoch_prediction=epoch_hourly_pred,
            hat_lat_prediction=hat_lat_pred,
        )

        datum_by_epoch[ep.name] = datum
        harmonics_by_epoch[ep.name] = harmonics_summary
        minute_highlow = pd.DataFrame(columns=["time", "height_mm", "type"])

        observed = fit_observed[["time", "sea_level"]].copy()
        datum_plot = _plot_datums(
            plot_dir / f"{ep.name}_datums.png",
            sub,
            datum,
            switch_levels,
            f"{record_id} {ep.name}: tidal datums",
        )
        within_epoch_pred = epoch_hourly_pred.copy()
        merged = observed.merge(within_epoch_pred, on="time", how="inner")
        residual_meta = _compute_residual_metrics(merged)
        compare_meta = _plot_hourly_comparison(
            plot_dir / f"{ep.name}_hourly_observed_vs_predicted.png",
            merged,
            f"{record_id} {ep.name}: observed vs predicted",
        )
        residual_plot = _plot_residuals(
            plot_dir / f"{ep.name}_hourly_residuals.png",
            merged,
            f"{record_id} {ep.name}: hourly residuals",
        )
        epoch_rmse = residual_meta["epoch_rmse_mm"]
        epoch_rmse_text = "nan" if epoch_rmse is None else f"{epoch_rmse:.2f}"
        log(
            f"{record_id} {ep.name}: saved harmonic artifact and plots; "
            f"constituents={len(harmonics.constituent)}, "
            f"epoch_rmse={epoch_rmse_text} mm"
        )

        minute_plot = None
        minute_rows = int(len(minute_highlow))
        if station_kind == "FD" and not minute_highlow.empty:
            minute_plot = _plot_fd_high_low(
                plot_dir / f"{ep.name}_fd_high_low.png",
                minute_highlow,
                f"{record_id} {ep.name}: FD high/low minute prediction",
            )

        epoch_summary = {
            "epoch": asdict(ep),
            "datum": asdict(datum),
            "harmonic_constituent_count": int(len(harmonics.constituent)),
            "harmonic_mean_mm": float(harmonics.mean_mm),
            "harmonic_slope_mm_per_day": float(harmonics.slope_mm_per_day),
            "top_constituents": harmonics.constituent[:12],
            "harmonic_artifact": harmonic_artifacts[ep.name],
            "fit_begin": fit_begin,
            "fit_end": fit_end,
            "fit_observation_rows": int(len(fit_observed)),

            "hourly_prediction_rows": 0,
            "hourly_prediction_variable": None,
            "hourly_prediction_start": None,
            "hourly_prediction_end": None,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,

            "hourly_observed_rows": int(len(observed)),
            "hourly_overlap_rows": int(len(merged)),
            "epoch_residual_rows": residual_meta["residual_rows"],

            "plots": {
                "datums": datum_plot,
                "hourly_observed_vs_predicted": compare_meta["comparison_plot"],
                "hourly_residuals": residual_plot,
                "fd_high_low": minute_plot,
            },

            "plot_window_rows": compare_meta["plot_window_rows"],
            "plot_window_rmse_mm": compare_meta["plot_window_rmse_mm"],
            "epoch_rmse_mm": residual_meta["epoch_rmse_mm"],
            "epoch_residual_mean_mm": residual_meta["epoch_residual_mean_mm"],
            "epoch_residual_std_mm": residual_meta["epoch_residual_std_mm"],
            "epoch_residual_abs_p95_mm": residual_meta["epoch_residual_abs_p95_mm"],

            "fd_high_low_rows": minute_rows,
            "minute_highlow_prediction_rows": 0,
            "minute_highlow_time_variable": None,
            "minute_highlow_height_variable": None,
            "minute_highlow_type_variable": None,

            "switch_levels": None if switch_levels is None else asdict(switch_levels),
        }

        epoch_summary_by_name[ep.name] = epoch_summary
        epoch_summaries.append(epoch_summary)
        del harmonics, sub, fit_observed, epoch_hourly_pred, hat_lat_pred, minute_highlow
        gc.collect()

    prediction_items = saved_prediction_epoch_items(epochs, prediction_plan)

    log(f"{record_id}: generating saved hourly predictions for {len(prediction_items)} epoch(s)")

    for prediction_key, ep in prediction_items:
        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])

        log(f"{record_id} {ep.name}: generating saved hourly prediction as {prediction_key}")

        hourly_predictions[prediction_key] = predict_from_harmonics(
            harmonics,
            prediction_plan.hourly_start,
            prediction_plan.hourly_end,
            freq="1h",
        )
        hourly_predictions[prediction_key].attrs["epoch_name"] = ep.name

        hourly_rows = int(len(hourly_predictions[prediction_key]))
        hourly_variable = f"hourly_prediction_{prediction_key}"

        hourly_prediction_summary = {
            "saved": True,
            "epoch": ep.name,
            "prediction_key": prediction_key,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,
            "variable": hourly_variable,
            "start": prediction_plan.hourly_start,
            "end": prediction_plan.hourly_end,
            "rows": hourly_rows,
        }
        hourly_prediction_summaries.append(hourly_prediction_summary)

        if ep.name in epoch_summary_by_name:
            epoch_summary_by_name[ep.name]["hourly_prediction_rows"] = hourly_rows
            epoch_summary_by_name[ep.name]["hourly_prediction_variable"] = hourly_variable
            epoch_summary_by_name[ep.name]["hourly_prediction_start"] = prediction_plan.hourly_start
            epoch_summary_by_name[ep.name]["hourly_prediction_end"] = prediction_plan.hourly_end

        del harmonics
        gc.collect()


    minute_items = saved_minute_highlow_epoch_items(epochs, prediction_plan)

    if minute_items:
        log(f"{record_id}: generating saved minute high/low predictions for {len(minute_items)} epoch(s)")

    for prediction_key, ep in minute_items:
        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])

        log(f"{record_id} {ep.name}: generating saved minute high/low prediction as {prediction_key}")

        minute_highlow_by_epoch[prediction_key] = predict_minute_high_low(
            harmonics,
            start=prediction_plan.minute_start,
            end=prediction_plan.minute_end,
        )

        minute_rows = int(len(minute_highlow_by_epoch[prediction_key]))

        minute_summary = {
            "saved": True,
            "epoch": ep.name,
            "prediction_key": prediction_key,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,
            "time_variable": f"minute_highlow_time_{prediction_key}",
            "height_variable": f"minute_highlow_height_mm_{prediction_key}",
            "type_variable": f"minute_highlow_type_{prediction_key}",
            "start": prediction_plan.minute_start,
            "end": prediction_plan.minute_end,
            "rows": minute_rows,
        }
        minute_highlow_prediction_summaries.append(minute_summary)

        if ep.name in epoch_summary_by_name:
            epoch_summary_by_name[ep.name]["minute_highlow_prediction_rows"] = minute_rows
            epoch_summary_by_name[ep.name]["minute_highlow_time_variable"] = minute_summary["time_variable"]
            epoch_summary_by_name[ep.name]["minute_highlow_height_variable"] = minute_summary["height_variable"]
            epoch_summary_by_name[ep.name]["minute_highlow_type_variable"] = minute_summary["type_variable"]

        del harmonics
        gc.collect()

    ds = build_netcdf_dataset(
        record_id,
        station_name,
        station_kind,
        epochs,
        datum_by_epoch,
        harmonics_by_epoch,
        hourly_predictions,
        switch_levels=switch_levels,
        prediction_plan=prediction_plan,
    )
    for ep_name, hl in minute_highlow_by_epoch.items():
        if not hl.empty:
            ds[f"minute_highlow_time_{ep_name}"] = ([f"minute_hl_{ep_name}"], hl["time"].to_numpy(dtype="datetime64[ns]"))
            ds[f"minute_highlow_height_mm_{ep_name}"] = ([f"minute_hl_{ep_name}"], np.rint(hl["height_mm"].to_numpy(dtype=float)).astype(np.int32))
            ds[f"minute_highlow_type_{ep_name}"] = ([f"minute_hl_{ep_name}"], hl["type"].astype(str).to_numpy())

    nc_path = outdir / "netcdf" / f"{record_id}.nc"
    nc_path.parent.mkdir(parents=True, exist_ok=True)
    save_netcdf(ds, str(nc_path))
    log(f"{record_id}: wrote NetCDF {nc_path}")

    return {
        "record_id": record_id,
        "station_name": station_name,
        "station_kind": station_kind,
        "netcdf": str(nc_path),
        "observation_start": observation_start,
        "observation_end": observation_end,
        "harmonic_artifacts": harmonic_artifacts,
        "prediction_basis_epoch": prediction_plan.basis_epoch,
        "prediction_scope": prediction_plan.prediction_scope,
        "hourly_prediction": next(
            item for item in hourly_prediction_summaries if item["is_prediction_basis"]
        ),
        "hourly_predictions": hourly_prediction_summaries,

        "minute_highlow_prediction": (
            next(
                item for item in minute_highlow_prediction_summaries
                if item["is_prediction_basis"]
            )
            if minute_highlow_prediction_summaries
            else {
                "saved": False,
                "time_variable": None,
                "height_variable": None,
                "type_variable": None,
                "start": None,
                "end": None,
                "rows": 0,
            }
        ),
        "minute_highlow_predictions": minute_highlow_prediction_summaries,
        "update_cycle_months": prediction_plan.update_cycle_months,
        "update_cycle_reason": prediction_plan.update_cycle_reason,
        "epochs": epoch_summaries,
        # Private runtime-only payloads for DB writers. These must be removed before
        # summary.json is written.
        "_hourly_prediction_frames": hourly_predictions,
        "_minute_highlow_frames": minute_highlow_by_epoch,
    }


def _run_station(station_id: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_root = project_root / "artifacts" / f"station{station_id}"
    output_root.mkdir(parents=True, exist_ok=True)
    run_last_update = pd.Timestamp.utcnow().tz_localize(None)
    log(f"Starting station {station_id} datums_predictions run")
    rq_versions = list_rq_versions([station_id])[station_id]
    log(f"Station {station_id}: RQ versions: {', '.join(rq_versions) if rq_versions else 'none'}")

    reconciliation = _build_station_source_reconciliation(
        station_id=station_id,
        erddap_rq_versions=rq_versions,
    )

    if reconciliation is not None:
        _log_station_source_reconciliation(reconciliation)
        rq_versions_for_run = reconciliation.rq_versions_to_process
    else:
        rq_versions_for_run = rq_versions

    summary = {
        "station_id": station_id,
        "records": [],
        "skipped_records": [],
        "source_reconciliation": None if reconciliation is None else asdict(reconciliation),
    }

    processed_any_record = False
    fd_epoch_sync: EpochSyncResult | None = None

    try:
        fd_record = _run_record(station_id, "FD", output_root)

    except ErddapNoRowsError as exc:
        if (
            DATABASE_POLICY.require_fd_record
            or DATABASE_POLICY.reconciliation_mode == "strict"
        ):
            raise

        log(
            f"Station {station_id}: FD/best_available record skipped in warn mode | "
            f"no hourly FD/best-available rows available from ERDDAP: {exc}"
        )

        summary["skipped_records"].append(
            {
                "record_id": station_id,
                "station_kind": "FD",
                "reason": (
                    "No hourly fast-delivery/best-available source rows were "
                    "available from ERDDAP."
                ),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )

    except NoQualifyingEpochError as exc:
        if (
            DATABASE_POLICY.require_fd_record
            or DATABASE_POLICY.reconciliation_mode == "strict"
        ):
            raise

        log(
            f"Station {station_id}: FD/best_available record skipped in warn mode | "
            f"source rows were available but no analysis epoch qualified: {exc}"
        )

        summary["skipped_records"].append(
            {
                "record_id": station_id,
                "station_kind": "FD",
                "reason": (
                    "Hourly fast-delivery/best-available source rows were "
                    "available, but no configured analysis epoch met the "
                    "duration/completion requirements."
                ),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )

    else:
        fd_epoch_sync, fd_datum_sync, fd_constituent_sync = (
            _sync_record_core_tables_to_database(
                fd_record,
                last_update=run_last_update,
            )
        )
        fd_record["database_sync"] = (
            None if fd_epoch_sync is None else asdict(fd_epoch_sync)
        )
        fd_record["database_datum_sync"] = (
            None if fd_datum_sync is None else asdict(fd_datum_sync)
        )
        fd_record["database_constituent_sync"] = (
            None
            if fd_constituent_sync is None
            else asdict(fd_constituent_sync)
        )

        fd_prediction_window = _resolve_record_prediction_db_window(
            record=fd_record,
            epoch_sync=fd_epoch_sync,
        )

        fd_tide_prediction_sync = _sync_record_tide_predictions_to_database(
            fd_record,
            epoch_sync=fd_epoch_sync,
            prediction_window=fd_prediction_window,
        )
        fd_record["database_tide_prediction_sync"] = (
            None
            if fd_tide_prediction_sync is None
            else asdict(fd_tide_prediction_sync)
        )

        fd_hf_tide_prediction_sync = (
            _sync_record_hf_tide_predictions_to_database(
                fd_record,
                epoch_sync=fd_epoch_sync,
                prediction_window=fd_prediction_window,
            )
        )
        fd_record["database_hf_tide_prediction_sync"] = (
            None
            if fd_hf_tide_prediction_sync is None
            else asdict(fd_hf_tide_prediction_sync)
        )

        fd_high_low_prediction_sync = (
            _sync_record_high_low_predictions_to_database(
                fd_record,
                epoch_sync=fd_epoch_sync,
                prediction_window=fd_prediction_window,
            )
        )
        fd_record["database_high_low_prediction_sync"] = (
            None
            if fd_high_low_prediction_sync is None
            else asdict(fd_high_low_prediction_sync)
        )

        fd_stale_recent_cleanup = _run_stale_recent_epoch_cleanup(
            fd_record,
            epoch_sync=fd_epoch_sync,
        )
        fd_record["database_stale_recent_epoch_cleanup"] = (
            None
            if fd_stale_recent_cleanup is None
            else asdict(fd_stale_recent_cleanup)
        )

        fd_record.pop("_hourly_prediction_frames", None)
        fd_record.pop("_minute_highlow_frames", None)

        summary["records"].append(fd_record)
        processed_any_record = True

    for version in rq_versions_for_run:
        rq_record_id = f"{station_id}{str(version).lower()}"

        try:
            rq_record = _run_record(station_id, "RQ", output_root, version=version)

        except ErddapNoRowsError as exc:
            if DATABASE_POLICY.reconciliation_mode == "strict":
                raise

            log(
                f"Station {station_id}: RQ record {rq_record_id} skipped in warn mode | "
                f"no hourly RQ rows available from ERDDAP for version {version}: {exc}"
            )

            summary["skipped_records"].append(
                {
                    "record_id": rq_record_id,
                    "station_kind": "RQ",
                    "version": str(version).upper(),
                    "reason": (
                        "RQ metadata/DB inventory listed this version, but no hourly "
                        "research-quality source rows were available from ERDDAP."
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

            continue

        except NoQualifyingEpochError as exc:
            if DATABASE_POLICY.reconciliation_mode == "strict":
                raise

            log(
                f"Station {station_id}: RQ record {rq_record_id} skipped in warn mode | "
                f"source rows were available but no analysis epoch qualified: {exc}"
            )

            summary["skipped_records"].append(
                {
                    "record_id": rq_record_id,
                    "station_kind": "RQ",
                    "version": str(version).upper(),
                    "reason": (
                        "Hourly research-quality source rows were available, but "
                        "no configured analysis epoch met the duration/completion "
                        "requirements."
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

            continue

        rq_epoch_sync, rq_datum_sync, rq_constituent_sync = (
            _sync_record_core_tables_to_database(
                rq_record,
                last_update=run_last_update,
            )
        )
        rq_record["database_sync"] = None if rq_epoch_sync is None else asdict(rq_epoch_sync)
        rq_record["database_datum_sync"] = (
            None if rq_datum_sync is None else asdict(rq_datum_sync)
        )
        rq_record["database_constituent_sync"] = (
            None if rq_constituent_sync is None else asdict(rq_constituent_sync)
        )

        rq_prediction_window = _resolve_record_prediction_db_window(
            record=rq_record,
            epoch_sync=rq_epoch_sync,
        )

        rq_tide_prediction_sync = _sync_record_tide_predictions_to_database(
            rq_record,
            epoch_sync=rq_epoch_sync,
            prediction_window=rq_prediction_window,
        )
        rq_record["database_tide_prediction_sync"] = (
            None if rq_tide_prediction_sync is None else asdict(rq_tide_prediction_sync)
        )

        rq_hf_tide_prediction_sync = _sync_record_hf_tide_predictions_to_database(
            rq_record,
            epoch_sync=rq_epoch_sync,
            prediction_window=rq_prediction_window,
        )
        rq_record["database_hf_tide_prediction_sync"] = (
            None
            if rq_hf_tide_prediction_sync is None
            else asdict(rq_hf_tide_prediction_sync)
        )

        rq_high_low_prediction_sync = _sync_record_high_low_predictions_to_database(
            rq_record,
            epoch_sync=rq_epoch_sync,
            prediction_window=rq_prediction_window,
        )
        rq_record["database_high_low_prediction_sync"] = (
            None if rq_high_low_prediction_sync is None else asdict(rq_high_low_prediction_sync)
        )

        rq_stale_recent_cleanup = _run_stale_recent_epoch_cleanup(
            rq_record,
            epoch_sync=rq_epoch_sync,
        )
        rq_record["database_stale_recent_epoch_cleanup"] = (
            None
            if rq_stale_recent_cleanup is None
            else asdict(rq_stale_recent_cleanup)
        )

        rq_record.pop("_hourly_prediction_frames", None)
        rq_record.pop("_minute_highlow_frames", None)

        summary["records"].append(rq_record)
        processed_any_record = True

    if not processed_any_record:
        raise RuntimeError(
            f"Station {station_id}: no FD or RQ records were successfully processed."
        )

    prediction_auto_cleanup = _run_auto_best_available_prediction_cleanup(
        fd_epoch_sync=fd_epoch_sync,
    )
    summary["prediction_auto_cleanup"] = (
        None if prediction_auto_cleanup is None else asdict(prediction_auto_cleanup)
    )

    # Optional manual override cleanup. Prefer the automatic cleanup above once
    # date_range_by_time_series_quality is being maintained reliably.
    _run_prediction_cutover_cleanup()

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    md_lines = [
        f"# Station {station_id} Datums Predictions",
        "",
        f"Output root: `{output_root}`",
        "",
    ]

    if reconciliation is not None:
        md_lines.extend(
            [
                "## Source reconciliation",
                f"- Status: `{reconciliation.status}`",
                f"- DB time_series identity versions: `{', '.join(reconciliation.db_time_series_versions) if reconciliation.db_time_series_versions else 'none'}`",
                f"- DB rq/hourly versions from date_range_by_time_series_quality: `{', '.join(reconciliation.db_rq_versions) if reconciliation.db_rq_versions else 'none'}`",
                f"- ERDDAP metadata RQ versions: `{', '.join(reconciliation.erddap_rq_versions) if reconciliation.erddap_rq_versions else 'none'}`",
                f"- RQ versions processed: `{', '.join(reconciliation.rq_versions_to_process) if reconciliation.rq_versions_to_process else 'none'}`",
                f"- time_series versions without DB rq/hourly availability: `{', '.join(reconciliation.db_time_series_without_rq_versions) if reconciliation.db_time_series_without_rq_versions else 'none'}`",
                f"- DB rq/hourly versions missing from ERDDAP metadata: `{', '.join(reconciliation.db_only_rq_versions) if reconciliation.db_only_rq_versions else 'none'}`",
                f"- ERDDAP metadata RQ versions ignored because DB has no rq/hourly range: `{', '.join(reconciliation.erddap_metadata_only_rq_versions) if reconciliation.erddap_metadata_only_rq_versions else 'none'}`",
                f"- ERDDAP metadata RQ versions without time_series identity rows: `{', '.join(reconciliation.erddap_versions_without_time_series) if reconciliation.erddap_versions_without_time_series else 'none'}`",
                f"- FD/best_available source `{reconciliation.fd_source_record_id}` resolved to `time_series_id={reconciliation.fd_time_series_id}`, `id_from_source={reconciliation.fd_id_from_source}`",
                "",
            ]
        )

    skipped_records = summary.get("skipped_records") or []

    if skipped_records:
        md_lines.extend(
            [
                "## Skipped records",
            ]
        )

        for skipped in skipped_records:
            md_lines.append(
                f"- `{skipped['record_id']}` / `{skipped['station_kind']}`: "
                f"{skipped['reason']} "
                f"(`{skipped['error_type']}: {skipped['error']}`)"
            )

        md_lines.append("")

    prediction_auto_cleanup = summary.get("prediction_auto_cleanup")

    if prediction_auto_cleanup is not None:
        md_lines.extend(
            [
                "## Prediction auto cleanup",
                f"- Current target: `{prediction_auto_cleanup['current_id_from_source']}` "
                f"(`time_series_id={prediction_auto_cleanup['current_time_series_id']}`)",
                f"- Actions planned: `{len(prediction_auto_cleanup['actions'])}`",
                f"- Write cleanup: `{prediction_auto_cleanup['write_cleanup']}`",
                f"- Tide prediction rows deleted: `{prediction_auto_cleanup['tide_prediction_rows_deleted']}`",
                f"- HF tide prediction rows deleted: `{prediction_auto_cleanup['hf_tide_prediction_rows_deleted']}`",
                f"- High/low prediction rows deleted: `{prediction_auto_cleanup['high_low_prediction_rows_deleted']}`",
            ]
        )

        if prediction_auto_cleanup.get("notes"):
            md_lines.append(
                "- Notes: `"
                + " | ".join(prediction_auto_cleanup["notes"])
                + "`"
            )

        for action in prediction_auto_cleanup["actions"]:
            md_lines.append(
                f"- `{action['id_from_source']}` keep window: "
                f"`{action['valid_start']}` to `{action['valid_end']}`"
            )

        md_lines.append("")

    for record in summary["records"]:
        md_lines.append(f"## {record['record_id']}")
        md_lines.append(f"- Station name: {record['station_name']}")
        md_lines.append(f"- Station kind: {record['station_kind']}")
        md_lines.append(f"- NetCDF: `{record['netcdf']}`")

        db_sync = record.get("database_sync")

        if db_sync is None:
            md_lines.append("- Database sync: none")
        else:
            md_lines.append(
                "- Database sync: "
                f"`time_series_id={db_sync['time_series_id']}`, "
                f"`id_from_source={db_sync['id_from_source']}`, "
                f"`input_basis={db_sync['input_basis_code']}`, "
                f"`resolution_rule={db_sync['resolution_rule']}`, "
                f"`write_epochs={db_sync['write_epochs']}`"
            )

            if db_sync.get("epoch_id_by_name"):
                epoch_id_text = ", ".join(
                    f"{name}={epoch_id}"
                    for name, epoch_id in db_sync["epoch_id_by_name"].items()
                )
                md_lines.append(f"- Database epoch IDs: `{epoch_id_text}`")
            else:
                md_lines.append("- Database epoch IDs: none; dry-run/no-write mode")

        datum_sync = record.get("database_datum_sync")

        if datum_sync is None:
            md_lines.append("- Database datum sync: none")
        else:
            md_lines.append(
                "- Database datum sync: "
                f"`rows_planned={datum_sync['rows_planned']}`, "
                f"`rows_written={datum_sync['rows_written']}`, "
                f"`write_datums={datum_sync['write_datums']}`"
            )

            if datum_sync.get("datum_id_by_epoch_and_short_name"):
                for epoch_name, datum_ids in datum_sync[
                    "datum_id_by_epoch_and_short_name"
                ].items():
                    datum_id_text = ", ".join(
                        f"{short_name}={datum_id}"
                        for short_name, datum_id in sorted(datum_ids.items())
                    )
                    md_lines.append(
                        f"- Database datum IDs for `{epoch_name}`: `{datum_id_text}`"
                    )
            else:
                md_lines.append("- Database datum IDs: none; dry-run/no-write mode")

        constituent_sync = record.get("database_constituent_sync")

        if constituent_sync is None:
            md_lines.append("- Database constituent sync: none")
        else:
            md_lines.append(
                "- Database constituent sync: "
                f"`rows_planned={constituent_sync['rows_planned']}`, "
                f"`rows_written={constituent_sync['rows_written']}`, "
                f"`missing_definitions={len(constituent_sync['missing_definition_short_names'])}`, "
                f"`write_constituents={constituent_sync['write_constituents']}`"
            )

            if constituent_sync.get("missing_definition_short_names"):
                md_lines.append(
                    "- Missing constituent definitions: `"
                    + ", ".join(constituent_sync["missing_definition_short_names"])
                    + "`"
                )

            if constituent_sync.get("constituent_id_by_epoch_and_short_name"):
                for epoch_name, constituent_ids in constituent_sync[
                    "constituent_id_by_epoch_and_short_name"
                ].items():
                    md_lines.append(
                        f"- Database constituent IDs for `{epoch_name}`: "
                        f"`{len(constituent_ids)} row(s)`"
                    )
            else:
                md_lines.append(
                    "- Database constituent IDs: none; dry-run/no-write mode"
                )

        tide_prediction_sync = record.get("database_tide_prediction_sync")

        if tide_prediction_sync is None:
            md_lines.append("- Database tide prediction sync: none")
        else:
            md_lines.append(
                "- Database tide prediction sync: "
                f"`rows_planned={tide_prediction_sync['rows_planned']}`, "
                f"`rows_deleted={tide_prediction_sync['rows_deleted']}`, "
                f"`rows_written={tide_prediction_sync['rows_written']}`, "
                f"`write_tide_predictions={tide_prediction_sync['write_tide_predictions']}`"
            )

            if tide_prediction_sync.get("notes"):
                md_lines.append(
                    "- Database tide prediction notes: `"
                    + " | ".join(tide_prediction_sync["notes"])
                    + "`"
                )

        hf_tide_prediction_sync = record.get("database_hf_tide_prediction_sync")

        if hf_tide_prediction_sync is None:
            md_lines.append("- Database HF tide prediction sync: none")
        else:
            md_lines.append(
                "- Database HF tide prediction sync: "
                f"`resolution={hf_tide_prediction_sync['temporal_resolution_code']}`, "
                f"`rows_planned={hf_tide_prediction_sync['rows_planned']}`, "
                f"`rows_deleted={hf_tide_prediction_sync['rows_deleted']}`, "
                f"`rows_written={hf_tide_prediction_sync['rows_written']}`, "
                f"`write_hf_tide_predictions={hf_tide_prediction_sync['write_hf_tide_predictions']}`"
            )

            if hf_tide_prediction_sync.get("notes"):
                md_lines.append(
                    "- Database HF tide prediction notes: `"
                    + " | ".join(hf_tide_prediction_sync["notes"])
                    + "`"
                )

        high_low_prediction_sync = record.get("database_high_low_prediction_sync")

        if high_low_prediction_sync is None:
            md_lines.append("- Database high/low prediction sync: none")
        else:
            md_lines.append(
                "- Database high/low prediction sync: "
                f"`rows_planned={high_low_prediction_sync['rows_planned']}`, "
                f"`rows_deleted={high_low_prediction_sync['rows_deleted']}`, "
                f"`rows_written={high_low_prediction_sync['rows_written']}`, "
                f"`write_high_low_predictions={high_low_prediction_sync['write_high_low_predictions']}`"
            )

            if high_low_prediction_sync.get("notes"):
                md_lines.append(
                    "- Database high/low prediction notes: `"
                    + " | ".join(high_low_prediction_sync["notes"])
                    + "`"
                )

        stale_recent_cleanup = record.get(
            "database_stale_recent_epoch_cleanup"
        )

        if stale_recent_cleanup is None:
            md_lines.append("- Database stale RECENT epoch cleanup: none")
        else:
            md_lines.append(
                "- Database stale RECENT epoch cleanup: "
                f"`stale_epochs={len(stale_recent_cleanup['stale_epochs'])}`, "
                f"`epochs_deleted={stale_recent_cleanup['epoch_rows_deleted']}`, "
                f"`tide_rows_deleted={stale_recent_cleanup['tide_prediction_rows_deleted']}`, "
                f"`hf_tide_rows_deleted={stale_recent_cleanup['hf_tide_prediction_rows_deleted']}`, "
                f"`high_low_rows_deleted={stale_recent_cleanup['high_low_prediction_rows_deleted']}`, "
                f"`write_cleanup={stale_recent_cleanup['write_cleanup']}`"
            )

        md_lines.append(f"- Prediction basis epoch: `{record['prediction_basis_epoch']}`")
        md_lines.append(f"- Prediction scope: `{record['prediction_scope']}`")
        hourly_predictions_summary = record.get("hourly_predictions") or [record["hourly_prediction"]]

        if len(hourly_predictions_summary) == 1:
            hourly = hourly_predictions_summary[0]
            md_lines.append(
                f"- Saved hourly prediction: `{hourly['variable']}` from "
                f"`{hourly['start']}` to `{hourly['end']}` ({hourly['rows']} rows)"
            )
        else:
            md_lines.append("- Saved hourly predictions:")
            for hourly in hourly_predictions_summary:
                basis_label = " [prediction basis]" if hourly.get("is_prediction_basis") else ""
                md_lines.append(
                    f"  - `{hourly['epoch']}`{basis_label}: `{hourly['variable']}` from "
                    f"`{hourly['start']}` to `{hourly['end']}` ({hourly['rows']} rows)"
                )

        minute_predictions_summary = record.get("minute_highlow_predictions") or []

        if minute_predictions_summary:
            if len(minute_predictions_summary) == 1:
                minute = minute_predictions_summary[0]
                md_lines.append(
                    "- Saved minute high/low prediction: "
                    f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                    f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                )
            else:
                md_lines.append("- Saved minute high/low predictions:")
                for minute in minute_predictions_summary:
                    basis_label = " [prediction basis]" if minute.get("is_prediction_basis") else ""
                    md_lines.append(
                        f"  - `{minute['epoch']}`{basis_label}: "
                        f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                        f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                    )
        else:
            minute = record.get("minute_highlow_prediction")
            if minute and minute.get("saved"):
                md_lines.append(
                    "- Saved minute high/low prediction: "
                    f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                    f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                )
            else:
                md_lines.append("- Saved minute high/low prediction: none")
        md_lines.append(f"- Update cycle: {record['update_cycle_months']} months ({record['update_cycle_reason']})")
        md_lines.append("- Saved epochs/datums/harmonics:")
        for epoch in record["epochs"]:
            epoch_rmse = epoch["epoch_rmse_mm"]
            epoch_rmse_text = "nan" if epoch_rmse is None else f"{epoch_rmse:.2f}"

            md_lines.append(
                f"- {epoch['epoch']['name']}: constituents={epoch['harmonic_constituent_count']}, "
                f"hourly_overlap_rows={epoch['hourly_overlap_rows']}, "
                f"full_epoch_rmse_mm={epoch_rmse_text}, "
                f"plot_window_rows={epoch['plot_window_rows']}"
            )
        md_lines.append("")
    (output_root / "summary.md").write_text("\n".join(md_lines))
    log(f"Station {station_id}: wrote summaries to {output_root / 'summary.json'} and {output_root / 'summary.md'}")
    log(f"Finished station {station_id} datums_predictions run")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run datums/predictions diagnostics for one, multiple, or all stations."
    )
    parser.add_argument(
        "--station-id",
        default=DEFAULT_STATION_ID,
        help="Station id, comma-separated ids, or 'all'.",
    )
    args = parser.parse_args()

    _validate_database_write_policy_or_die()

    station_ids = _resolve_station_ids(args.station_id)
    log(
        f"Requested datums_predictions run for {len(station_ids)} "
        f"station(s): {', '.join(station_ids)}"
    )

    failed_stations: list[str] = []

    for station_id in station_ids:
        try:
            _run_station(station_id)
        except ErddapUnavailableError as exc:
            log(f"Station {station_id} aborted due to temporary ERDDAP failure: {exc}")
            failed_stations.append(station_id)

    if failed_stations:
        log(
            "Datums_predictions run completed with temporary ERDDAP failures "
            f"for station(s): {', '.join(failed_stations)}"
        )
        raise SystemExit(1)

if __name__ == "__main__":
    main()
