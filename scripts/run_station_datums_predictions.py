from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import sys
import gc
import os
import re
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidal_config import DATABASE_POLICY

from core import (
    build_prediction_save_plan,
    build_netcdf_dataset,
    clean_hourly_dataframe,
    compute_datums,
    fetch_fd_hourly,
    fetch_rq_hourly,
    fit_harmonics,
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
class StationSourceReconciliation:
    station_id: str
    db_rq_versions: list[str]
    erddap_rq_versions: list[str]
    rq_versions_to_process: list[str]
    db_only_rq_versions: list[str]
    erddap_only_rq_versions: list[str]
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
        db_rq_versions = sorted(
            {
                version
                for row in rows
                for version in [
                    _version_from_id_from_source(station_id, row.id_from_source)
                ]
                if version is not None
            }
        )

        erddap_versions = sorted(str(version).upper() for version in erddap_rq_versions)

        db_only = sorted(set(db_rq_versions) - set(erddap_versions))
        erddap_only = sorted(set(erddap_versions) - set(db_rq_versions))
        to_process = sorted(set(db_rq_versions).intersection(erddap_versions))

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

        status = "ok"

        if db_only or erddap_only:
            status = "source_gaps"

        if fd_time_series_id is None:
            status = "source_gaps"

        return StationSourceReconciliation(
            station_id=station_id,
            db_rq_versions=db_rq_versions,
            erddap_rq_versions=erddap_versions,
            rq_versions_to_process=to_process,
            db_only_rq_versions=db_only,
            erddap_only_rq_versions=erddap_only,
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
        f"DB RQ versions={reconciliation.db_rq_versions or 'none'} | "
        f"ERDDAP RQ versions={reconciliation.erddap_rq_versions or 'none'} | "
        f"to_process={reconciliation.rq_versions_to_process or 'none'} | "
        f"status={reconciliation.status}"
    )

    log(
        f"Station {reconciliation.station_id}: FD/best_available source "
        f"{reconciliation.fd_source_record_id} resolves to "
        f"time_series_id={reconciliation.fd_time_series_id}, "
        f"id_from_source={reconciliation.fd_id_from_source}"
    )

    if reconciliation.db_only_rq_versions:
        log(
            f"Station {reconciliation.station_id}: DB-only RQ versions missing from "
            f"ERDDAP and skipped: {', '.join(reconciliation.db_only_rq_versions)}"
        )

    if reconciliation.erddap_only_rq_versions:
        log(
            f"Station {reconciliation.station_id}: ERDDAP-only RQ versions missing from "
            f"DB and not safe for DB writes: "
            f"{', '.join(reconciliation.erddap_only_rq_versions)}"
        )

    if DATABASE_POLICY.reconciliation_mode == "strict" and reconciliation.status != "ok":
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
                "epoch_name": ep["name"],
                "epoch_source": ep["source"],
                "epoch_role": ep["role"],
                "fit_begin": pd.Timestamp(ep["start"]),
                "fit_end": pd.Timestamp(ep["end"]),
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
            f"source={ep['source']} | role={ep['role']}"
        )


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
    epochs = select_epochs(df)
    if not epochs:
        raise RuntimeError(f"No qualifying epochs found for {record_id}")
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
        fitted_harmonics = fit_harmonics(sub, latitude=latitude)
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
                "latitude": float(latitude),
            },
        )
        harmonics_summary = strip_harmonic_result(fitted_harmonics)
        del fitted_harmonics
        gc.collect()

        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])
        epoch_hourly_pred = predict_from_harmonics(harmonics, ep.start, ep.end, freq="1h")
        datum = compute_datums(sub, epoch_prediction=epoch_hourly_pred)

        datum_by_epoch[ep.name] = datum
        harmonics_by_epoch[ep.name] = harmonics_summary
        minute_highlow = pd.DataFrame(columns=["time", "height_mm", "type"])

        observed = sub.dropna(subset=["sea_level"])[["time", "sea_level"]].copy()
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
        del harmonics, sub, epoch_hourly_pred, minute_highlow
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
    }


def _run_station(station_id: str) -> None:
    output_root = Path("artifacts") / "datums_predictions" / f"station{station_id}"
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
        "source_reconciliation": None if reconciliation is None else asdict(reconciliation),
    }

    fd_record = _run_record(station_id, "FD", output_root)
    fd_epoch_sync = _sync_record_epochs_to_database(
        fd_record,
        last_update=run_last_update,
    )
    fd_record["database_sync"] = None if fd_epoch_sync is None else asdict(fd_epoch_sync)
    summary["records"].append(fd_record)

    for version in rq_versions_for_run:
        rq_record = _run_record(station_id, "RQ", output_root, version=version)
        rq_epoch_sync = _sync_record_epochs_to_database(
            rq_record,
            last_update=run_last_update,
        )
        rq_record["database_sync"] = None if rq_epoch_sync is None else asdict(rq_epoch_sync)
        summary["records"].append(rq_record)

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
                f"- DB RQ versions: `{', '.join(reconciliation.db_rq_versions) if reconciliation.db_rq_versions else 'none'}`",
                f"- ERDDAP RQ versions: `{', '.join(reconciliation.erddap_rq_versions) if reconciliation.erddap_rq_versions else 'none'}`",
                f"- RQ versions processed: `{', '.join(reconciliation.rq_versions_to_process) if reconciliation.rq_versions_to_process else 'none'}`",
                f"- DB-only RQ versions skipped: `{', '.join(reconciliation.db_only_rq_versions) if reconciliation.db_only_rq_versions else 'none'}`",
                f"- ERDDAP-only RQ versions unsafe for DB writes: `{', '.join(reconciliation.erddap_only_rq_versions) if reconciliation.erddap_only_rq_versions else 'none'}`",
                f"- FD/best_available source `{reconciliation.fd_source_record_id}` resolved to `time_series_id={reconciliation.fd_time_series_id}`, `id_from_source={reconciliation.fd_id_from_source}`",
                "",
            ]
        )
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
    parser = argparse.ArgumentParser(description="Run datums/predictions diagnostics for one, multiple, or all stations.")
    parser.add_argument("--station-id", default=DEFAULT_STATION_ID, help="Station id, comma-separated ids, or 'all'.")
    args = parser.parse_args()

    station_ids = _resolve_station_ids(args.station_id)
    log(f"Requested datums_predictions run for {len(station_ids)} station(s): {', '.join(station_ids)}")
    for station_id in station_ids:
        _run_station(station_id)


if __name__ == "__main__":
    main()
