# Station 002 Datums Predictions

Output root: `artifacts/datums_predictions/station002`

## Source reconciliation
- Status: `ok`
- DB RQ versions: `A, B, C, D`
- ERDDAP RQ versions: `A, B, C, D`
- RQ versions processed: `A, B, C, D`
- DB-only RQ versions skipped: `none`
- ERDDAP-only RQ versions unsafe for DB writes: `none`
- FD/best_available source `002` resolved to `time_series_id=180`, `id_from_source=000002D`

## 002
- Station name: Tarawa, Bairiki
- Station kind: FD
- NetCDF: `artifacts/datums_predictions/station002/netcdf/002.nc`
- Database sync: `time_series_id=180`, `id_from_source=000002D`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1992-12-04 01:00:00` to `2100-12-31 23:00:00` (947375 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=160999, full_epoch_rmse_mm=72.87, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=169999, full_epoch_rmse_mm=82.68, plot_window_rows=744
- RECENT_2007-04-30_2026-04-30: constituents=68, hourly_overlap_rows=163537, full_epoch_rmse_mm=74.16, plot_window_rows=744

## 002a
- Station name: Tarawa, Betio
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station002/netcdf/002a.nc`
- Database sync: `time_series_id=221`, `id_from_source=000002A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `RECENT_1974-05-03_1983-12-31`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1974-05-03 05:00:00` to `1983-12-31 10:00:00` (84702 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1974-05-03_1983-12-31: constituents=68, hourly_overlap_rows=66551, full_epoch_rmse_mm=83.58, plot_window_rows=744

## 002b
- Station name: Tarawa, Bairiki
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station002/netcdf/002b.nc`
- Database sync: `time_series_id=595`, `id_from_source=000002B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `RECENT_1983-05-17_1988-05-10`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1983-05-17 07:00:00` to `1988-05-10 03:00:00` (43677 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1983-05-17_1988-05-10: constituents=68, hourly_overlap_rows=42810, full_epoch_rmse_mm=83.20, plot_window_rows=744

## 002c
- Station name: Tarawa, Betio
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station002/netcdf/002c.nc`
- Database sync: `time_series_id=449`, `id_from_source=000002C`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `RECENT_1988-01-20_1997-12-31`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1988-01-20 05:00:00` to `1997-12-31 23:00:00` (87211 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1988-01-20_1997-12-31: constituents=68, hourly_overlap_rows=87159, full_epoch_rmse_mm=78.35, plot_window_rows=744

## 002d
- Station name: Tarawa, Betio
- Station kind: RQ
- NetCDF: `artifacts/datums_predictions/station002/netcdf/002d.nc`
- Database sync: `time_series_id=180`, `id_from_source=000002D`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1992-12-04 01:00:00` to `2100-12-31 23:00:00` (947375 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=160999, full_epoch_rmse_mm=72.87, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=169999, full_epoch_rmse_mm=82.68, plot_window_rows=744
- RECENT_2002-12-31_2021-12-31: constituents=68, hourly_overlap_rows=160771, full_epoch_rmse_mm=72.63, plot_window_rows=744
