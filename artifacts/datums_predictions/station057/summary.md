# Station 057 Datums Predictions

Output root: `artifacts/datums_predictions/station057`

## Source reconciliation
- Status: `ok`
- DB RQ versions: `A, B`
- ERDDAP RQ versions: `A, B`
- RQ versions processed: `A, B`
- DB-only RQ versions skipped: `none`
- ERDDAP-only RQ versions unsafe for DB writes: `none`
- FD/best_available source `057` resolved to `time_series_id=617`, `id_from_source=000057B`

## 057
- Station name: Honolulu, Hawaii
- Station kind: FD
- NetCDF: `artifacts/datums_predictions/station057/netcdf/057.nc`
- Database sync: `time_series_id=617`, `id_from_source=000057B`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1905-01-01 10:00:00` to `2100-12-31 23:00:00` (1718102 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8440 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=166560, full_epoch_rmse_mm=63.97, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=164899, full_epoch_rmse_mm=60.08, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=175320, full_epoch_rmse_mm=54.05, plot_window_rows=744
- RECENT_2007-04-30_2026-04-30: constituents=68, hourly_overlap_rows=166383, full_epoch_rmse_mm=63.76, plot_window_rows=744

## 057a
- Station name: Honolulu, Hawaii
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station057/netcdf/057a.nc`
- Database sync: `time_series_id=544`, `id_from_source=000057A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `RECENT_1891-03-13_1892-07-13`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1877-06-21 10:00:00` to `1892-07-13 09:00:00` (132024 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1891-03-13_1892-07-13: constituents=67, hourly_overlap_rows=9288, full_epoch_rmse_mm=39.63, plot_window_rows=744

## 057b
- Station name: Honolulu, Hawaii
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station057/netcdf/057b.nc`
- Database sync: `time_series_id=617`, `id_from_source=000057B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1905-01-01 10:00:00` to `2100-12-31 23:00:00` (1718102 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8440 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=166560, full_epoch_rmse_mm=63.97, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=164899, full_epoch_rmse_mm=60.08, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=175320, full_epoch_rmse_mm=54.05, plot_window_rows=744
- RECENT_2002-12-31_2021-12-31: constituents=68, hourly_overlap_rows=166561, full_epoch_rmse_mm=63.08, plot_window_rows=744
