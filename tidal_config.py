from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FixedEpochSpec:
    """A named, fixed calendar epoch tested before dynamic standard epochs."""

    name: str
    start: pd.Timestamp
    end: pd.Timestamp
    source: str = "primary"
    role: str = "datum"


@dataclass(frozen=True)
class EpochPolicy:
    """Rules for selecting datum/harmonic epochs from a station record."""

    fixed_epochs: tuple[FixedEpochSpec, ...]
    # A candidate epoch must have at least this fraction of expected hourly
    # observations present before it can be selected.
    min_completion_fraction: float = 0.75
    # RECENT is a dynamic standard epoch, not just a fallback. When enough data
    # exist it is selected after the fixed epochs and any PREDICTION
    # (abbreviated PRED) epoch, so its datums and harmonics are saved alongside
    # other qualifying epochs.
    min_recent_months: int = 3
    # RECENT should represent the latest available data without exceeding the
    # 19-year length used by standard tidal datum epochs.
    max_recent_years: int = 19
    # Most records should only need periodic regeneration after a new NTDE-era
    # amount of data has accumulated.
    default_update_years: int = 5
    # Short, still-active records need more frequent reruns because new data can
    # materially change the selected RECENT epoch and harmonic fit.
    short_record_update_months: int = 3
    short_record_years: int = 5
    short_record_recent_end_year: int = 2025


@dataclass(frozen=True)
class PredictionPolicy:
    """Rules for saved prediction products.

    By default, only the selected prediction_basis_epoch is saved as the
    record-level prediction product. Set save_predictions_for_all_epochs=True
    to save one prediction series for every selected epoch while still marking
    prediction_basis_epoch as the default/recommended basis.
    """

    # When False, saved products match the current behavior: only the selected
    # prediction_basis_epoch is written as hourly_prediction_primary.
    #
    # When True, saved hourly predictions are written for every selected epoch.
    # When minute high/low predictions are active for a record, they are also
    # written for every selected epoch.
    save_predictions_for_all_epochs: bool = False

    # FD and most-recent RQ versions save hourly predictions into the future.
    long_hourly_end: pd.Timestamp = pd.Timestamp("2100-12-31 23:00:00")

    # Minute high/low predictions use a moving operational window. For example,
    # runtime 2026 with -1/+4 offsets gives 2025-01-01 through 2030-12-31.
    minute_runtime_start_offset_years: int = -1
    minute_runtime_end_offset_years: int = 4


@dataclass(frozen=True)
class DatabasePolicy:
    """Rules for optional database synchronization.

    Default behavior is safe: database writes are disabled. When writes are
    disabled, the processing script should still log what would be written.
    """

    write_epochs: bool = False
    log_epoch_plan: bool = True

    # Datum writes depend on epoch sync results. Datum rows are written by
    # epoch_id and time_series_id, using the epoch database sync manifest as the
    # source of truth for identity.
    write_datums: bool = False
    log_datum_plan: bool = True

    # Constituent writes depend on epoch sync results. Constituent rows are written
    # by epoch_id and time_series_id, using the epoch database sync manifest as the
    # source of truth for identity.
    write_constituents: bool = False
    log_constituent_plan: bool = True

    # Hourly tide prediction writes depend on epoch sync results. Values are written
    # in meters, rounded before insert/update.
    write_tide_predictions: bool = False
    log_tide_prediction_plan: bool = True

    # Minute high/low prediction writes. FD products use the generated operational
    # high/low window. RQ products are bounded by the DB-authoritative rq/hourly
    # range from date_range_by_time_series_quality.
    write_high_low_predictions: bool = False
    log_high_low_prediction_plan: bool = True

    # Database prediction values are stored in meters. The generated prediction
    # products are currently in millimeters.
    prediction_value_mm_to_database_meters: float = 0.001
    prediction_value_decimal_places: int = 4

    # Temporal resolution lookup for public.tide_prediction.
    hourly_temporal_resolution_code: str = "hourly"

    # Batch size for large prediction inserts.
    prediction_insert_batch_size: int = 10000

    # Optional explicit best_available cutover cleanup.
    #
    # Each entry is:
    #   (old_id_from_source, new_id_from_source, cutover_time)
    #
    # Example:
    #   ("000014D", "000014E", "2026-05-01 00:00:00")
    #
    # This is intentionally explicit. The code should not guess a D->E boundary.
    prediction_cutover_cleanups: tuple[tuple[str, str, str], ...] = ()
    write_prediction_cutover_cleanup: bool = False
    log_prediction_cutover_cleanup_plan: bool = True

    # Names used to query public.date_range_by_time_series_quality.
    #
    # fd is used for best_available/current-stream cleanup.
    # rq is used to bound research_quality prediction writes.
    fd_record_quality_short_name: str = "fd"
    rq_record_quality_short_name: str = "rq"

    # Automatic cleanup for stale rolling RECENT_* epochs on the same current
    # time_series/input_basis target.
    #
    # This removes old RECENT_* epochs that are no longer selected by the current
    # run for the same time_series_id + input_basis_id, along with their datum,
    # constituent, tide_prediction, and high_low_prediction child rows. Stable
    # named epochs such as NTDE_* and IPCC-* are intentionally not deleted.
    auto_cleanup_stale_recent_epochs: bool = False
    write_stale_recent_epoch_cleanup: bool = False
    log_stale_recent_epoch_cleanup_plan: bool = True

    # Automatic cleanup for superseded best_available prediction rows.
    #
    # This uses public.date_range_by_time_series_quality as the authoritative
    # source of valid FD/best_available date ranges. It does not guess from the
    # latest version suffix.
    #
    # When the unversioned FD source resolves to the current target, earlier FD
    # targets for the same station/priority are treated as superseded. Their
    # tide_prediction and high_low_prediction rows are trimmed to their valid
    # materialized-view date_begin/date_end windows.
    auto_cleanup_superseded_best_available_predictions: bool = False
    write_prediction_auto_cleanup: bool = False
    log_prediction_auto_cleanup_plan: bool = True

    # Some stations may have RQ/versioned records but no usable FD/unversioned
    # record. In warn mode, allow the station run to skip FD and continue with
    # exact RQ records. In strict mode, FD failures still raise.
    require_fd_record: bool = False

    # Compare the database's station/version inventory with the ERDDAP records
    # available to this run before any epoch rows are written.
    reconcile_station_inventory: bool = True

    # How to handle DB/ERDDAP inventory gaps discovered during reconciliation.
    #
    # "warn":
    #   Continue with records that have both ERDDAP source observations and a
    #   safe DB target. DB-only records are reported/skipped.
    #
    # "strict":
    #   Raise before processing the station if a DB-authoritative rq/hourly version
    #   is missing from ERDDAP metadata, or if the FD/best_available DB target cannot
    #   be resolved. FD-only stations are valid when DB has no rq/hourly date ranges.
    #   ERDDAP metadata-only RQ versions are reported but ignored because DB
    #   date_range_by_time_series_quality is authoritative for RQ availability.
    reconciliation_mode: str = "warn"

    # Fail epoch writes unless the target time_series row can be resolved from
    # a deterministic rule.
    #
    # RQ records:
    #   Must match exact id_from_source, e.g. 014a -> 000014A.
    #
    # FD/best_available records:
    #   Use the DB utility resolver for the unversioned ERDDAP stream, e.g.
    #   014 -> whichever DB row the database currently says represents that
    #   published best_available stream.
    require_exact_epoch_write_target: bool = True

    # FD/unlettered records are best-available products.
    fd_input_basis_code: str = "best_available"

    # RQ/lettered records are research-quality products.
    rq_input_basis_code: str = "research_quality"

    # Optional direct path to the directory containing env_utils.py and
    # timescale_utils.py. If set, this wins over all environment inference.
    timescale_utils_dir: str | None = None

    # Fallback environment if PROCESS_ENV is not exported.
    process_env_default: str = "dev"

    # Timescale process homes by environment. The utilities are expected under
    # <process_home>/utils.
    timescale_process_home_by_env: tuple[tuple[str, str], ...] = (
        ("dev", "/home/nwstg/timescale"),
        ("prod", "/home/uhslc/timescale/current"),
    )


# Selection hierarchy:
# 1. Fixed standard epochs below are tested in the listed order.
# 2. PREDICTION (abbreviated PRED) may add one PRED_YYYY_YYYY epoch when fixed
#    epochs pass total completion but none pass the annual-completion check
#    needed for a stable prediction basis.
# 3. RECENT is then tested as a dynamic standard epoch based on the latest
#    qualifying data span, up to 19 years.
#
# prediction_basis_epoch is selected as PRED_* first when it exists, otherwise
# the first selected fixed epoch in this list, otherwise RECENT. By default,
# only prediction_basis_epoch is saved as the record-level prediction product.
# Set PREDICTION_POLICY.save_predictions_for_all_epochs=True below to save
# epoch-specific prediction products for every selected epoch while preserving
# prediction_basis_epoch as the default/recommended basis. Since RECENT is also
# selected when it qualifies, a record can have up to five selected epochs.
EPOCH_POLICY = EpochPolicy(
    fixed_epochs=(
        FixedEpochSpec(
            "NTDE_2002-2020",
            pd.Timestamp("2002-01-01 00:00:00"),
            pd.Timestamp("2020-12-31 23:00:00"),
        ),
        FixedEpochSpec(
            "NTDE_1983-2001",
            pd.Timestamp("1983-01-01 00:00:00"),
            pd.Timestamp("2001-12-31 23:00:00"),
        ),
        FixedEpochSpec(
            "IPCC-AR6_1995-2014",
            pd.Timestamp("1995-01-01 00:00:00"),
            pd.Timestamp("2014-12-31 23:00:00"),
        ),
    )
)

# Define whether to save predictions for all epochs or only the prediction_basis_epoch.
PREDICTION_POLICY = PredictionPolicy(
    save_predictions_for_all_epochs=False,
)

# NOAA CO-OPS defines HAT/LAT as the highest/lowest predicted astronomical tide
# over a 40-year period, updated every 20 years. The current published period is
# 2000-2040. Keep this configurable so the next official window can be changed
# without touching the processing code.
HAT_LAT_PREDICTION_START = pd.Timestamp("2000-01-01 00:00:00")
HAT_LAT_PREDICTION_END = pd.Timestamp("2040-12-31 23:00:00")

# Define what datasets to write to the database.
DATABASE_POLICY = DatabasePolicy(
    write_epochs=False,
    write_datums=False,
    write_constituents=False,
    write_tide_predictions=False,
    write_high_low_predictions=False,
    auto_cleanup_stale_recent_epochs=False,
    write_stale_recent_epoch_cleanup=False,
    auto_cleanup_superseded_best_available_predictions=False,
    write_prediction_auto_cleanup=False,
    write_prediction_cutover_cleanup=False,
    reconciliation_mode="warn",
)


def minute_prediction_window(
    runtime: pd.Timestamp | None = None,
    policy: PredictionPolicy = PREDICTION_POLICY,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the operational minute high/low prediction window for a run."""
    run_time = pd.Timestamp.now() if runtime is None else pd.Timestamp(runtime)
    start_year = run_time.year + policy.minute_runtime_start_offset_years
    end_year = run_time.year + policy.minute_runtime_end_offset_years
    return (
        pd.Timestamp(year=start_year, month=1, day=1, hour=0, minute=0),
        pd.Timestamp(year=end_year, month=12, day=31, hour=23, minute=59),
    )
