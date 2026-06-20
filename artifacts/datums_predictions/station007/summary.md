# Station 007 Datums Predictions

Output root: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007`

## Source reconciliation
- Status: `ok`
- DB time_series identity versions: `A, B`
- DB rq/hourly versions from date_range_by_time_series_quality: `A, B`
- ERDDAP metadata RQ versions: `A, B`
- RQ versions processed: `A, B`
- time_series versions without DB rq/hourly availability: `none`
- DB rq/hourly versions missing from ERDDAP metadata: `none`
- ERDDAP metadata RQ versions ignored because DB has no rq/hourly range: `none`
- ERDDAP metadata RQ versions without time_series identity rows: `none`
- FD/best_available source `007` resolved to `time_series_id=315`, `id_from_source=000007B`

## 007
- Station name: Malakal
- Station kind: FD
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007.nc`
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Database datum sync: `rows_planned=0`, `rows_written=0`, `write_datums=False`
- Database datum IDs: none; dry-run/no-write mode
- Database constituent sync: `rows_planned=0`, `rows_written=0`, `missing_definitions=0`, `write_constituents=False`
- Database constituent IDs: none; dry-run/no-write mode
- Database tide prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_tide_predictions=False`
- Database tide prediction notes: `No epoch IDs available.`
- Database high/low prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_high_low_predictions=False`
- Database high/low prediction notes: `No epoch IDs available.`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1969-05-18 15:00:00` to `2100-12-31 23:00:00` (1153785 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=164206, full_epoch_rmse_mm=124.10, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=165292, full_epoch_rmse_mm=128.05, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=173651, full_epoch_rmse_mm=129.12, plot_window_rows=744
- RECENT_2007-04-30_2026-04-30: constituents=68, hourly_overlap_rows=165253, full_epoch_rmse_mm=116.96, plot_window_rows=744

## 007a
- Station name: Malakal
- Station kind: RQ
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007a.nc`
- Database sync: `time_series_id=372`, `id_from_source=000007A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Database datum sync: `rows_planned=0`, `rows_written=0`, `write_datums=False`
- Database datum IDs: none; dry-run/no-write mode
- Database constituent sync: `rows_planned=0`, `rows_written=0`, `missing_definitions=0`, `write_constituents=False`
- Database constituent IDs: none; dry-run/no-write mode
- Database tide prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_tide_predictions=False`
- Database tide prediction notes: `No epoch IDs available.`
- Database high/low prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_high_low_predictions=False`
- Database high/low prediction notes: `No epoch IDs available.`
- Prediction basis epoch: `RECENT_1926-01-01_1939-12-10`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1926-01-01 01:00:00` to `1939-12-10 15:00:00` (122199 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1926-01-01_1939-12-10: constituents=68, hourly_overlap_rows=113090, full_epoch_rmse_mm=117.85, plot_window_rows=744

## 007b
- Station name: Malakal
- Station kind: RQ
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007b.nc`
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=False`
- Database epoch IDs: none; dry-run/no-write mode
- Database datum sync: `rows_planned=0`, `rows_written=0`, `write_datums=False`
- Database datum IDs: none; dry-run/no-write mode
- Database constituent sync: `rows_planned=0`, `rows_written=0`, `missing_definitions=0`, `write_constituents=False`
- Database constituent IDs: none; dry-run/no-write mode
- Database tide prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_tide_predictions=False`
- Database tide prediction notes: `No epoch IDs available.`
- Database high/low prediction sync: `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_high_low_predictions=False`
- Database high/low prediction notes: `No epoch IDs available.`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1969-05-18 15:00:00` to `2100-12-31 23:00:00` (1153785 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=146662, full_epoch_rmse_mm=126.86, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=165292, full_epoch_rmse_mm=128.05, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=173651, full_epoch_rmse_mm=129.12, plot_window_rows=744
- RECENT_1999-12-31_2018-12-31: constituents=68, hourly_overlap_rows=164207, full_epoch_rmse_mm=123.35, plot_window_rows=744
