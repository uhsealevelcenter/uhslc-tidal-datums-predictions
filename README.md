# UHSLC Tidal Datums and Predictions Handoff Package

## Purpose
This package contains the current prototype implementation developed from the uploaded instructions for:
- tidal datum calculation,
- harmonic fitting,
- tide prediction generation,
- NetCDF output,
- and batch-style processing for UHSLC FD/RQ station records.

It is prepared for handoff into another repository or another computer.

## Included Files

### Core scripts
- `core.py` — main processing logic
- `tidal_batch.py` — command-line driver
- `scripts/run_station_datums_predictions.py` — datums/predictions runner for one, multiple, or all stations
- `scripts/run_station_datums_only.py` — datums-only runner for one, multiple, or all stations
- `scripts/update_switch_levels.py` — refresh cached `LEV`/`LEVB` elevations from the live `.din` directory
- `tests/test_core_unittest.py` — unit tests using `unittest`

### Documentation
- `docs/HumanPreparedInstructions_TidalDatumsPredictions.md` — Markdown transcription of the human-authored instruction document
- `docs/instructions_extracted.txt` — raw extracted text from the original instruction document
- `docs/instructions_summary.txt` — line-numbered instruction dump
- `docs/rq_full_span_probe_001.json` — evidence from RQ ERDDAP probing for station 001
- `docs/rq_full_span_probe_002.json` — evidence from RQ ERDDAP probing for station 002
- `docs/fd_run_summary.json` — summary from FD real-data prototype runs

### Artifacts
- `artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md` — Skill library entry referenced by generated NetCDFs
- `artifacts/datums_predictions/station###/` — full datums/predictions outputs, including NetCDFs, harmonics, plots, and summaries
- `artifacts/datums_only/station###/` — datums-only diagnostic outputs, including datums-only NetCDFs, harmonics, plots, and summaries

## Environment Assumptions
This prototype was developed in Python 3.11 and used:
- `numpy`
- `pandas`
- `xarray`
- `netCDF4`
- `scipy`
- `matplotlib`
- `utide`
- `requests`
- `PyYAML`

Suggested install example:

```bash
pip install numpy pandas xarray netCDF4 scipy matplotlib utide requests pyyaml
```

## Current Functional Status

### Working
- Unit tests pass for the synthetic/prototype workflow.
- Real FD ERDDAP loading works.
- Switch elevations are now cached from the live `.din` metadata directory into `data/switch_levels.csv` and included in NetCDF/plots when available as `LEV` and `LEVB`.
- NetCDF outputs now include Skill reference metadata pointing to `artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md`, so downstream LLM services can find the vetted workflow without duplicating the Skill body in every file.
- Full available FD ERDDAP span for station `001` was confirmed.
- Real FD prototype outputs were successfully generated earlier for stations `001`, `002`, `003`, and `007` using shorter operational windows.
- Full RQ ERDDAP spans were confirmed for station `002` versions `A`, `B`, `C`, `D`.
- Live loader integration tests now pass for station `007` FD and RQ versions `A` and `B`.
- Saved predictions are now record-level products based on the primary prediction epoch.
- Station diagnostic runners now accept `--station-id` and write to `artifacts/datums_predictions/station###/` and `artifacts/datums_only/station###/`.

### Known limitations
- Full-record end-to-end processing for FD `001` was killed by the OS (`return code -9`), likely due to resource pressure in the current implementation.
- RQ availability through ERDDAP appears inconsistent for station `001`; live metadata may list versions that are not currently exposed by ERDDAP.
- The switch-elevation source is still an interim `.din` directory, so this part of the workflow should later be redirected to a more permanent location.
- The harmonic implementation now matches the core legacy UTide setup more closely, but it is still not a full legacy-equivalent production workflow.
- The current code should still be monitored for memory pressure during long UTide solves, even though prediction generation now uses persisted harmonic artifacts and chunked FD minute extraction.

## Recommended Next Refactors
1. **Process full records lazily/in chunks** instead of building very large in-memory arrays.
2. **Continue reducing solve-time memory pressure** during epoch harmonic fitting, which remains the main peak-memory step.
3. **Refine RQ mapping** to reconcile ERDDAP exposure vs live metadata records.
4. **Expand harmonic constituents** and align more closely with legacy software behavior.
5. **Add integration tests** for real ERDDAP station runs.

## Epoch Selection Summary
- Configured epoch hierarchy is `NTDE_2002-2020`, `NTDE_1983-2001`, `IPCC-AR6_1995-2014`, `PREDICTION (abbreviated PRED)`, and `RECENT`.
- Primary epochs are accepted when total hourly data completion is at least 75%.
- If at least one primary epoch qualifies but no qualifying primary epoch has at least 75% completion in every calendar year, the code may add one `PREDICTION (abbreviated PRED)` epoch named `PRED_YYYY_YYYY` when a 19-calendar-year window meets the 75% annual threshold in every year.
- `PRED_*` epochs are tagged with `epoch_source = prediction` and `epoch_role = harmonic_prediction`; they preserve standard datum comparability while allowing a better-conditioned harmonic fit for prediction.
- `RECENT_*` is a dynamic standard epoch selected after the fixed epochs and any `PREDICTION (abbreviated PRED)` epoch when the record has at least 3 months of sufficiently complete recent data, up to a 19-year span.
- A station record can have up to five selected epochs: three fixed epochs, one `PRED_*` epoch, and one `RECENT_*` epoch.
- All selected epochs calculate in-epoch predictions for datum calculations, but only the primary prediction epoch is used for saved prediction products.
- Saved prediction basis is `PRED_*` when present, otherwise the first selected fixed epoch in the configured hierarchy, otherwise `RECENT_*`.
- Update cadence is 5 years by default, or 3 months when the longest selected epoch is shorter than 5 years and the record ends in 2025 or later.

## Run Commands

The examples that generate plots set `MPLCONFIGDIR=/tmp/mplconfig` so
Matplotlib writes its config and font-cache files outside the repository and
avoids home-directory permission issues in sandboxed environments.

Use `scripts/run_station_datums_predictions.py` for the full `datums_predictions`
product. It runs FD plus all metadata-listed RQ versions for each requested
station, writes NetCDFs, harmonic artifacts, plots, `summary.json`, and
`summary.md`.

Use `scripts/run_station_datums_only.py` for the `datums_only` product. It runs
the same station/RQ record set but writes datums-only NetCDFs and summaries
without saved harmonic constituent variables or saved prediction series.

Both scripts accept a single station id, a comma-separated station list, or
`all`.

### Unit Tests
```bash
python3 -m unittest discover -s tests -v
```

### Live Loader Integration Test
```bash
RUN_LIVE_UHSLC=1 MPLCONFIGDIR=/tmp/mplconfig python3 -m unittest tests.test_live_station007 -v
```

### Refresh Switch Elevations
```bash
python scripts/update_switch_levels.py
```

Run this before generating real-data outputs when you want the latest available
`LEV`/`LEVB` values in NetCDFs and datum plots.

### Datums Predictions

Run one station:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_predictions.py --station-id 002
```

Outputs are written under `artifacts/datums_predictions/station002/`.

Run multiple stations:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_predictions.py --station-id 001,002,007
```

Outputs are written under one directory per station:

```text
artifacts/datums_predictions/station001/
artifacts/datums_predictions/station002/
artifacts/datums_predictions/station007/
```

Run all metadata-listed stations:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_predictions.py --station-id all
```

This can be long-running and network/resource intensive because it processes FD
and all listed RQ versions for every station.

### Datums Only

Run one station:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_only.py --station-id 002
```

Outputs are written under `artifacts/datums_only/station002/`.

Run multiple stations:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_only.py --station-id 001,002,007
```

Run all metadata-listed stations:

```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datums_only.py --station-id all
```

### Production-Style Single-Record CLI

`tidal_batch.py` processes one FD, RQ version, or CSV input at a time. Use this
when you need direct control over one output directory or one input record.

FD datums/predictions:

```bash
python tidal_batch.py --mode fd --station-id 001 --station-kind FD --output-dir outputs
```

FD datums only:

```bash
python tidal_batch.py --mode fd --station-id 001 --station-kind FD --output-dir outputs --datums-only
```

RQ datums/predictions:

```bash
python tidal_batch.py --mode rq --station-id 002 --station-kind RQ --version A --output-dir outputs
```

RQ datums only:

```bash
python tidal_batch.py --mode rq --station-id 002 --station-kind RQ --version A --output-dir outputs --datums-only
```

### Update Tide Type In Existing NetCDF Outputs
```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/update_netcdf_tide_type.py
MPLCONFIGDIR=/tmp/mplconfig python scripts/update_netcdf_tide_type.py --write
```

The first command is a dry-run. The `--write` form fetches observed hourly data
from ERDDAP and updates only the existing `tide_type` variable in matching
NetCDF files; it does not rerun harmonics, predictions, or plots.

## Important Notes for the Handoff Repo
- The current code relies on direct ERDDAP access to:
  - `global_hourly_fast`
  - `global_hourly_rqds`
- Station inventory, names, coordinates, and RQ version metadata can be loaded from:
  - `https://uhslc.soest.hawaii.edu/data/meta.geojson`
- Switch elevations (`LEV`, `LEVB`) are loaded from:
  - `https://uhslc.soest.hawaii.edu/mwidlans/dev/metadata/din/`
  - and cached locally in `data/switch_levels.csv`, which is only regenerated when older than 30 days
- NetCDF outputs also include Skill discovery attributes:
  - `skill_name`, `skill_description`, `skill_format`
  - `skill_local_path`, `skill_remote_url`, `skill_location`
- Datums-only NetCDF outputs set `content = datums_only` and omit harmonic constituent and prediction variables.
- If the receiving environment has stricter memory limits, full-record runs may need chunking immediately.
- The legacy Matlab instructions use UTide with epoch-wide solves, nodal corrections enabled, annual constituents enabled, and trend removed only at prediction time. The Python implementation now follows that same pattern.
- To reduce long-epoch memory and CPU pressure, the harmonic solve now follows the legacy Matlab `opt = 'nostats'` approach rather than computing UTide confidence intervals.
- FD and most-recent RQ records save hourly predictions from record start through `2100-12-31 23:00` and minute high/low predictions over the runtime policy window. Older RQ versions save only hourly predictions over record start through record end.
- For Python `utide`, pass datetime arrays directly into `solve()` and `reconstruct()`. Passing Matplotlib day numbers without an explicit epoch can yield empty constituent sets and invalid sampling diagnostics.
- Tide type is classified from observed hourly sea level using NOAA categories: `Diurnal`, `Semidiurnal`, or `Mixed Semidiurnal`. The classifier counts local high/low waters in valid 24h50m tidal-day windows and separates semidiurnal from mixed semidiurnal using within-window high/low inequality.

## Source Context
This package was assembled from work performed in an IDEA/SEA environment and is intended as a **prototype handoff**, not a final production release.
