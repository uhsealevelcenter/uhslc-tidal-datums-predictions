---
name: uhslc-tidal-datums-predictions
description: Recreate UHSLC tidal datums, harmonics, and predictions from original hourly station data using the vetted project workflow.
---

This skill explains how to reproduce the tidal datums and tide-prediction products contained in a UHSLC NetCDF from the original source data.

## Scope

Use this skill when recreating:

- epoch selection
- datum calculation
- harmonic fitting
- hourly tide prediction generation
- FD minute high/low extraction
- switch elevation handling
- standard fixed, PREDICTION (abbreviated PRED), and RECENT epoch decisions

## Inputs

Required inputs:

- hourly sea level observations in station-zero units
- station id
- station kind: FD or RQ
- station latitude
- switch elevations when available

Use GMT timestamps. Use millimeters for exported datum and prediction values.

## Epoch Selection

Configured epoch hierarchy:

- NTDE_2002-2020
- NTDE_1983-2001
- IPCC-AR6_1995-2014
- PREDICTION (abbreviated PRED)
- RECENT

Rules:

- clean the hourly series first
- drop duplicate timestamps
- treat missing sentinel values as null
- compute hourly completeness over each candidate epoch
- accept a standard epoch when at least 75 percent of expected hourly values are present
- select qualifying fixed epochs in hierarchy order

PREDICTION (abbreviated PRED) epoch:

- after accepting standard epochs, check whether any accepted standard epoch has at least 75 percent hourly completion in every calendar year within that epoch
- if none of the accepted standard epochs meets that annual criterion, add one `PRED_YYYY_YYYY` epoch when a 19-calendar-year window can meet at least 75 percent hourly completion in every year
- use `source = prediction` and `role = harmonic_prediction` for `PRED_YYYY_YYYY`
- keep the standard `NTDE_*` or `IPCC-AR6_*` epoch for datum comparability; the `PRED_*` epoch exists to support a better-conditioned harmonic fit for tide prediction
- this is rare, but because RECENT is also selected when it qualifies, a station record may have up to five epochs: three fixed epochs, one `PRED_*` epoch, and one `RECENT_*` epoch

RECENT epoch:

- RECENT is a dynamic standard epoch selected after the fixed epochs and any PREDICTION (abbreviated PRED) epoch
- require at least about three months of valid hourly data
- use the most recent qualifying data span
- do not exceed 19 full calendar years
- label it explicitly as `RECENT_*`

Update cadence:

- use a 5-year update cycle by default
- use a 3-month update cycle when the longest selected epoch is shorter than 5 years and the record ends in 2025 or later

## Datums

Compute datums from observed hourly sea level within the selected epoch.

Before datum calculation:

- sort observations by GMT timestamp
- drop duplicate timestamps
- convert the missing sentinel value `-32767` to null
- drop rows with null sea level values
- use the remaining valid hourly observations for datum calculations

For local-extrema datums:

- MHW and MLW are based on local extrema in the valid-observation sea-level array.
- Detect high waters as local maxima with a minimum separation of 6 hourly samples.
- Detect low waters as local minima with a minimum separation of 6 hourly samples.
- MHW is the arithmetic mean of all detected high-water values.
- MLW is the arithmetic mean of all detected low-water values.

For tidal-day datums:

- Use non-overlapping tidal-day windows of exactly 24 hours 50 minutes.
- Anchor the first window at the first valid timestamp in the epoch after missing values are dropped.
- Advance subsequent windows by exactly 24 hours 50 minutes.
- Use half-open window bounds: include observations with `time >= window_start` and `time < window_start + 24h50m`.
- Stop before the final partial tidal-day window; include only windows where `window_start + 24h50m <= last_valid_timestamp`.
- A window is valid only when it contains at least 20 finite hourly sea-level observations.
- MHHW is the arithmetic mean of the maximum observed hourly sea level in each valid tidal-day window.
- MLLW is the arithmetic mean of the minimum observed hourly sea level in each valid tidal-day window.
- Do not calculate MHHW and MLLW from the local-extrema high/low event lists; use the raw observed hourly values within each valid tidal-day window.

Derived datums:

- DTL = (MHHW + MLLW) / 2
- MTL = (MHW + MLW) / 2
- MSL = arithmetic mean of valid observed hourly sea level
- GT = MHHW - MLLW
- MN = MHW - MLW
- DHQ = MHHW - MHW
- DLQ = MLW - MLLW

Rounding:

- Keep datum calculations as floating-point values internally.
- Calculate derived datums from the unrounded floating-point values.
- Round final exported datum values to integer millimeters only when writing the NetCDF.

Tide type:

- Classify tide type from valid observed hourly sea level within non-overlapping tidal-day windows.
- Use the NOAA categories `Diurnal`, `Semidiurnal`, and `Mixed Semidiurnal`.
- `Diurnal` means the typical valid tidal-day window has one high water and one low water.
- `Semidiurnal` means the typical valid tidal-day window has two high waters and two low waters of approximately equal size.
- `Mixed Semidiurnal` means the typical valid tidal-day window has two high waters and two low waters of different size.
- Treat two highs or two lows as approximately equal when their median within-window height difference is no more than 10 percent of the median valid tidal-day range.

HAT and LAT should come from the harmonic tide prediction over the epoch, not directly from observations.

The percentile fields in this project are based on observed hourly sea level during the epoch in station-zero units.

## Harmonic Analysis

Fit harmonics over the full epoch in one solve using UTide-style harmonic analysis.

Guidance:

- clean the hourly data first, then drop null sea-level rows
- require at least 30 days of valid hourly observations before fitting
- use the station latitude
- pass datetime arrays directly to Python UTide rather than Matplotlib date numbers
- call `utide.solve()` with `trend=True`, `method='ols'`, `nodal=True`, `conf_int='none'`, and `verbose=False`
- retain the linear trend in the solve, matching the legacy epoch-wide analysis
- disable confidence-interval estimation with `conf_int='none'`, matching the legacy `nostats` workflow and reducing memory pressure
- do not mutate the fitted coefficients in place
- save both a JSON harmonic summary and a pickle artifact containing the reconstructable UTide coefficient object

The harmonic summary saved in the NetCDF is not by itself sufficient for later prediction unless the reconstructable harmonic state is also preserved elsewhere.

## Tide Predictions

Saved predictions are record-level products generated from the primary
prediction epoch. Non-primary epochs still calculate in-epoch predictions in
memory for datum calculation, including HAT and LAT, but do not save prediction
series.

Primary prediction epoch priority:

- use `PRED_*` first when present
- otherwise use the first selected fixed epoch in the configured hierarchy
- otherwise use `RECENT_*`

Hourly predictions:

- FD and most recent RQ version: predict from record start through 2100-12-31 23:00
- older RQ versions: predict from record start through record end

Minute high/low predictions:

- FD and most recent RQ version: generate minute predictions from the start of the year before runtime through the end of runtime year plus four. A runtime during 2026 gives 2025-01-01 00:00 through 2030-12-31 23:59
- older RQ versions: do not save minute high/low predictions
- save only extracted daily high/low event times and heights

When reconstructing predictions from harmonics:

- use the fitted constituent set
- reconstruct with `utide.reconstruct()` using the fitted constituent list and `min_SNR=0`
- remove trend during reconstruction by setting a copied coefficient object's slope to zero
- preserve the original fit object for reuse

## Switch Elevations

Switch elevations are optional station metadata:

- LEV: Switch 1 elevation
- LEVB: Switch 2 elevation

When available, include LEV and LEVB in the NetCDF and datum plots.

These values are currently derived from the most-current top switch row in the station .din metadata file and cached in switch_levels.csv.

## Edge Cases

For a case like 1982-2000 versus the standard NTDE_1983-2001:

- prefer the standard named epoch when it qualifies and the goal is comparability to published standard epochs
- prefer a nonstandard continuous epoch when the goal is the best stable harmonic fit from the available data and the span materially improves the fit
- if `NTDE_1983-2001` passes total completion but lacks at least 75 percent completion in every year, and `1982-2000` meets that annual threshold in every year, include both `NTDE_1983-2001` and `PRED_1982_2000`
- if a nonstandard span is chosen, label it explicitly as a recent/custom or prediction epoch rather than presenting it as the standard epoch

This means there can be reasonable disagreement in edge cases, but the dataset should always make the selected span explicit and reproducible.
