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
    #   Raise before processing the station if any DB/ERDDAP gap exists.
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

# Default behavior - save predictions only for the prediction_basis_epoch. 
PREDICTION_POLICY = PredictionPolicy()

# Optional - save predictions for all epochs.
# PREDICTION_POLICY = PredictionPolicy(
#     save_predictions_for_all_epochs=True,
# )

# Default behavior - do not write database products.
DATABASE_POLICY = DatabasePolicy()

# Optional - enable epoch writes to Timescale/Postgres.
# DATABASE_POLICY = DatabasePolicy(
#     write_epochs=True,
#     reconciliation_mode="warn",
# )


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
