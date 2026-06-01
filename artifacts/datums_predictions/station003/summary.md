# Station 003 Datums Predictions

Output root: `artifacts/datums_predictions/station003`

## Source reconciliation
- Status: `ok`
- DB RQ versions: `A, B`
- ERDDAP RQ versions: `A, B`
- RQ versions processed: `A, B`
- DB-only RQ versions skipped: `none`
- ERDDAP-only RQ versions unsafe for DB writes: `none`
- FD/best_available source `003` resolved to `time_series_id=52`, `id_from_source=000003B`

## 003
- Station name: Baltra
- Station kind: FD
- NetCDF: `artifacts/datums_predictions/station003/netcdf/003.nc`
- Database sync: `time_series_id=52`, `id_from_source=000003B`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1985-03-25 15:00:00` to `2100-12-31 23:00:00` (1014825 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=137471, full_epoch_rmse_mm=74.34, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=142802, full_epoch_rmse_mm=95.93, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=146468, full_epoch_rmse_mm=83.56, plot_window_rows=744
- RECENT_2007-04-30_2026-04-30: constituents=68, hourly_overlap_rows=158120, full_epoch_rmse_mm=98.08, plot_window_rows=744

## 003a
- Station name: Baltra, Galapagos
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station003/netcdf/003a.nc`
- Database sync: `time_series_id=1`, `id_from_source=000003A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `RECENT_1968-06-01_1977-12-31`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1968-06-01 06:00:00` to `1977-12-31 22:00:00` (84017 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1968-06-01_1977-12-31: constituents=68, hourly_overlap_rows=78266, full_epoch_rmse_mm=79.07, plot_window_rows=744

## 003b
- Station name: Baltra
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station003/netcdf/003b.nc`
- Database sync: `time_series_id=52`, `id_from_source=000003B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_1983-2001`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1985-03-25 15:00:00` to `2100-12-31 23:00:00` (1014825 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=142802, full_epoch_rmse_mm=95.93, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=146468, full_epoch_rmse_mm=83.56, plot_window_rows=744
- RECENT_1999-12-31_2018-12-31: constituents=68, hourly_overlap_rows=137389, full_epoch_rmse_mm=74.29, plot_window_rows=744
