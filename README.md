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
- `scripts/run_station_full_test.py` — full real-data diagnostic runner for one station
- `scripts/run_station_datum_test.py` — datums-only real-data diagnostic runner for one station
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
- `artifacts/full_test/station###/` — full diagnostic outputs, including NetCDFs, harmonics, plots, and summaries
- `artifacts/datum_test/station###/` — datums-only diagnostic outputs, including datums-only NetCDFs, harmonics, plots, and summaries

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
- FD outputs now save chunked minute-derived daily high/low prediction events through `2035-12-31 23:00`.
- Station diagnostic runners now accept `--station-id` and write to `artifacts/full_test/station###/` and `artifacts/datum_test/station###/`.

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
- Primary epochs are `NTDE_1983-2001`, `NTDE_2002-2020`, and `IPCC-AR6_1995-2014`.
- Primary epochs are accepted when total hourly data completion is at least 75%.
- If at least one primary epoch qualifies but no qualifying primary epoch has at least 75% completion in every calendar year, the code may add one prediction-specific `PRED_YYYY_YYYY` epoch when a 19-calendar-year window meets the 75% annual threshold in every year.
- `PRED_*` epochs are tagged with `epoch_source = prediction` and `epoch_role = harmonic_prediction`; they preserve standard datum comparability while allowing a better-conditioned harmonic fit for prediction.
- If no primary epoch qualifies, the code falls back to one most-recent `RECENT_*` epoch when the record has enough valid data.

## Basic Usage

The examples that generate plots set `MPLCONFIGDIR=/tmp/mplconfig` so
Matplotlib writes its config and font-cache files outside the repository and
avoids home-directory permission issues in sandboxed environments.

Use the station test scripts when you want diagnostics: they run FD plus all
available RQ versions for one station, write plots, save harmonic artifacts, and
produce summaries for review. Use `tidal_batch.py` when you want the
production-style command-line path for generating NetCDF products for a specific
FD, RQ, or CSV input.

### Run unit tests
```bash
python3 -m unittest discover -s tests -v
```

### Run live station 007 loader integration test
```bash
RUN_LIVE_UHSLC=1 MPLCONFIGDIR=/tmp/mplconfig python3 -m unittest tests.test_live_station007 -v
```

### Refresh cached switch elevations
```bash
python scripts/update_switch_levels.py
```

Run this before generating real-data outputs when you want the latest available
`LEV`/`LEVB` values in NetCDFs and datum plots.

### Run a full real-data station diagnostic
```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_full_test.py --station-id 002
```

Outputs are written under `artifacts/full_test/station002/`.

### Run a datums-only real-data station diagnostic
```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/run_station_datum_test.py --station-id 002
```

Outputs are written under `artifacts/datum_test/station002/`.

### Update tide type in existing NetCDF outputs
```bash
MPLCONFIGDIR=/tmp/mplconfig python scripts/update_netcdf_tide_type.py
MPLCONFIGDIR=/tmp/mplconfig python scripts/update_netcdf_tide_type.py --write
```

The first command is a dry-run. The `--write` form fetches observed hourly data
from ERDDAP and updates only the existing `tide_type` variable in matching
NetCDF files; it does not rerun harmonics, predictions, or plots.

### FD example
```bash
python tidal_batch.py --mode fd --station-id 001 --station-kind FD --output-dir outputs
```

### RQ example
```bash
python tidal_batch.py --mode rq --station-id 002 --station-kind RQ --version A --output-dir outputs
```

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
- FD hourly predictions are capped at `2035-12-31 23:00`, and FD minute predictions are reduced to saved daily high/low event times and heights over `2025-01-01 00:00` through `2030-12-31 23:59`.
- For Python `utide`, pass datetime arrays directly into `solve()` and `reconstruct()`. Passing Matplotlib day numbers without an explicit epoch can yield empty constituent sets and invalid sampling diagnostics.
- Tide type is classified from observed hourly sea level using NOAA categories: `Diurnal`, `Semidiurnal`, or `Mixed Semidiurnal`. The classifier counts local high/low waters in valid 24h50m tidal-day windows and separates semidiurnal from mixed semidiurnal using within-window high/low inequality.

## Source Context
This package was assembled from work performed in an IDEA/SEA environment and is intended as a **prototype handoff**, not a final production release.
