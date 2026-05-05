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
- standard-epoch versus fallback-epoch decisions

## Inputs

Required inputs:

- hourly sea level observations in station-zero units
- station id
- station kind: FD or RQ
- station latitude
- switch elevations when available

Use GMT timestamps. Use millimeters for exported datum and prediction values.

## Epoch Selection

Primary standard epochs are:

- NTDE_1983-2001
- NTDE_2002-2020
- IPCC-AR6_1995-2014

Rules:

- clean the hourly series first
- drop duplicate timestamps
- treat missing sentinel values as null
- compute hourly completeness over each candidate epoch
- accept a standard epoch when at least 75 percent of expected hourly values are present
- keep at most three epochs

Prediction-specific epoch:

- after accepting standard epochs, check whether any accepted standard epoch has at least 75 percent hourly completion in every calendar year within that epoch
- if none of the accepted standard epochs meets that annual criterion, add one `PRED_YYYY_YYYY` epoch when a 19-calendar-year window can meet at least 75 percent hourly completion in every year
- use `source = prediction` and `role = harmonic_prediction` for `PRED_YYYY_YYYY`
- keep the standard `NTDE_*` or `IPCC-AR6_*` epoch for datum comparability; the `PRED_*` epoch exists to support a better-conditioned harmonic fit for tide prediction
- this is rare, but a station record may therefore have up to four epochs

If no standard epoch qualifies:

- require at least about six months of valid hourly data
- define one most-recent fallback epoch
- do not exceed 19 years
- label it as a recent/custom epoch instead of silently relabeling it as a standard epoch

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

- use the station latitude
- keep nodal corrections enabled
- retain the linear trend in the solve
- disable confidence-interval estimation for the solve when mirroring the legacy nostats workflow
- do not mutate the fitted coefficients in place

The harmonic summary saved in the NetCDF is not by itself sufficient for later prediction unless the reconstructable harmonic state is also preserved elsewhere.

## Tide Predictions

Hourly predictions:

- FD: predict from epoch start through 2035-12-31 23:00
- RQ: predict only over the available epoch

FD minute predictions:

- generate minute predictions from 2025-01-01 00:00 through 2030-12-31 23:59
- save only extracted daily high/low event times and heights

When reconstructing predictions from harmonics:

- use the fitted constituent set
- remove trend during reconstruction
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
