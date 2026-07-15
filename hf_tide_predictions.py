"""High-frequency tide-prediction interpolation helpers.

The database HF prediction product is derived from the same saved hourly
prediction frames used by the regular tide-prediction writer.  This module is
intentionally database-agnostic so interpolation behavior can be tested without
TimescaleDB or the station runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterator

import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline


@dataclass(frozen=True)
class HourlyPredictionSpline:
    """Validated natural cubic spline built from one hourly prediction frame."""

    spline: CubicSpline
    origin: pd.Timestamp
    source_start: pd.Timestamp
    source_end: pd.Timestamp


def minute_resolution_timedelta(resolution_code: str) -> pd.Timedelta:
    """Parse a minute-based temporal-resolution code.

    Supported examples include ``minute``, ``1-minute``, ``5-minute``,
    ``1min``, and ``15 min``.  Non-minute resolutions are rejected because the
    HF tide-prediction product is defined on the station's HF minute grid.
    """

    value = str(resolution_code).strip().lower()
    match = re.fullmatch(r"(?:(\d+)\s*[-_ ]?\s*)?(?:minute|min)s?", value)

    if match is None:
        raise ValueError(
            "HF temporal resolution must be minute-based; "
            f"received {resolution_code!r}."
        )

    minutes = int(match.group(1) or 1)
    if minutes <= 0:
        raise ValueError(
            "HF temporal resolution must be a positive number of minutes; "
            f"received {resolution_code!r}."
        )

    return pd.Timedelta(minutes=minutes)


def _utc_naive_datetime_series(values: pd.Series) -> pd.Series:
    times = pd.to_datetime(values, errors="raise")

    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)

    return times


def build_hourly_prediction_spline(
    hourly_prediction: pd.DataFrame,
) -> HourlyPredictionSpline:
    """Build a natural cubic spline from a generated hourly prediction frame.

    The full-precision ``prediction_mm`` values are used.  The source frame must
    be a complete, unique, strictly hourly grid with finite predictions.
    """

    required_columns = {"time", "prediction_mm"}
    missing = required_columns - set(hourly_prediction.columns)
    if missing:
        raise ValueError(
            f"Hourly prediction frame is missing required columns: {sorted(missing)}"
        )

    frame = hourly_prediction.loc[:, ["time", "prediction_mm"]].copy()
    if frame.empty:
        raise ValueError("Hourly prediction frame is empty.")

    frame["time"] = _utc_naive_datetime_series(frame["time"])
    frame["prediction_mm"] = pd.to_numeric(
        frame["prediction_mm"], errors="coerce"
    )
    frame = frame.sort_values("time").reset_index(drop=True)

    if frame["time"].duplicated().any():
        duplicates = frame.loc[frame["time"].duplicated(), "time"].head(5).tolist()
        raise ValueError(
            "Hourly prediction frame contains duplicate timestamps; "
            f"examples={duplicates}."
        )

    values_mm = frame["prediction_mm"].to_numpy(dtype=float)
    if not np.isfinite(values_mm).all():
        raise ValueError("Hourly prediction frame contains non-finite predictions.")

    if len(frame) < 2:
        raise ValueError("At least two hourly prediction rows are required.")

    deltas = frame["time"].diff().dropna()
    bad_deltas = deltas[deltas != pd.Timedelta(hours=1)]
    if not bad_deltas.empty:
        raise ValueError(
            "Hourly prediction frame must be a complete 1-hour grid; "
            f"found interval(s)={sorted(set(map(str, bad_deltas.head(5))))}."
        )

    source_start = pd.Timestamp(frame["time"].iloc[0])
    source_end = pd.Timestamp(frame["time"].iloc[-1])
    origin = source_start
    x_old = (
        (frame["time"] - origin) / pd.Timedelta(seconds=1)
    ).to_numpy(dtype=float)

    return HourlyPredictionSpline(
        spline=CubicSpline(
            x_old,
            values_mm,
            bc_type="natural",
            extrapolate=False,
        ),
        origin=origin,
        source_start=source_start,
        source_end=source_end,
    )


def hf_prediction_row_count(
    start: pd.Timestamp,
    end: pd.Timestamp,
    interval: pd.Timedelta,
) -> int:
    """Return the inclusive row count for a fixed-frequency HF window."""

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    interval = pd.Timedelta(interval)

    if interval <= pd.Timedelta(0):
        raise ValueError("HF prediction interval must be positive.")
    if end < start:
        return 0

    return int((end - start) // interval) + 1


def iter_hf_prediction_chunks(
    hourly_prediction: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    interval: pd.Timedelta,
    chunk_days: int,
    mm_to_meters: float = 0.001,
    value_decimal_places: int = 4,
) -> Iterator[pd.DataFrame]:
    """Yield interpolated HF prediction chunks in database-ready units.

    A single natural cubic spline is fit to the full hourly source frame.  It is
    then evaluated in bounded chunks on the target minute grid.  Values are
    converted from millimeters to meters and rounded using the same database
    precision policy as regular tide predictions.
    """

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    interval = pd.Timedelta(interval)

    if chunk_days <= 0:
        raise ValueError("chunk_days must be a positive integer.")
    if interval <= pd.Timedelta(0):
        raise ValueError("HF prediction interval must be positive.")
    if end < start:
        return

    prepared = build_hourly_prediction_spline(hourly_prediction)

    if start < prepared.source_start or end > prepared.source_end:
        raise ValueError(
            "HF interpolation window must be contained in the hourly source "
            f"window. source={prepared.source_start} to {prepared.source_end}; "
            f"requested={start} to {end}."
        )

    chunk_span = pd.Timedelta(days=int(chunk_days))
    rows_per_chunk = max(1, int(chunk_span // interval))
    chunk_start = start

    while chunk_start <= end:
        chunk_end = min(
            end,
            chunk_start + interval * (rows_per_chunk - 1),
        )
        times = pd.date_range(
            start=chunk_start,
            end=chunk_end,
            freq=interval,
        )

        if len(times) == 0:
            break

        x_new = ((times - prepared.origin) / pd.Timedelta(seconds=1)).to_numpy(
            dtype=float
        )
        prediction_mm = np.asarray(prepared.spline(x_new), dtype=float)

        if not np.isfinite(prediction_mm).all():
            raise ValueError(
                "Natural cubic-spline evaluation produced non-finite HF values."
            )

        values_m = np.round(
            prediction_mm * float(mm_to_meters),
            int(value_decimal_places),
        )

        yield pd.DataFrame(
            {
                "time": times,
                "value": values_m,
            }
        )

        chunk_start = pd.Timestamp(times[-1]) + interval
