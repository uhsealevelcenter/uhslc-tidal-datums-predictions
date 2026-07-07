# Station 002 Datums Predictions

Output root: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station002`

## Source reconciliation
- Status: `ok`
- DB time_series identity versions: `A, B, C, D`
- DB rq/hourly versions from date_range_by_time_series_quality: `A, B, C, D`
- ERDDAP metadata RQ versions: `A, B, C, D`
- RQ versions processed: `A, B, C, D`
- time_series versions without DB rq/hourly availability: `none`
- DB rq/hourly versions missing from ERDDAP metadata: `none`
- ERDDAP metadata RQ versions ignored because DB has no rq/hourly range: `none`
- ERDDAP metadata RQ versions without time_series identity rows: `none`
- FD/best_available source `002` resolved to `time_series_id=180`, `id_from_source=000002D`

## Skipped records
- `002a` / `RQ`: RQ metadata/DB inventory listed this version, but no hourly research-quality source rows were available from ERDDAP. (`ErddapNoRowsError: Error {
    code=404;
    message="Not Found: Your query produced no matching results. (nRows = 0)";
}
`)
- `002b` / `RQ`: RQ metadata/DB inventory listed this version, but no hourly research-quality source rows were available from ERDDAP. (`ErddapNoRowsError: Error {
    code=404;
    message="Not Found: Your query produced no matching results. (nRows = 0)";
}
`)
- `002d` / `RQ`: RQ metadata/DB inventory listed this version, but no hourly research-quality source rows were available from ERDDAP. (`ErddapNoRowsError: Error {
    code=404;
    message="Not Found: Your query produced no matching results. (nRows = 0)";
}
`)

## 002
- Station name: Tarawa, Bairiki
- Station kind: FD
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station002/netcdf/002.nc`
- Database sync: `time_series_id=180`, `id_from_source=000002D`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=True`
- Database epoch IDs: `NTDE_2002-2020=1798, IPCC-AR6_1995-2014=1799, RECENT_2007-04-30_2026-04-30=1800`
- Database datum sync: `rows_planned=39`, `rows_written=0`, `write_datums=False`
- Database datum IDs: none; dry-run/no-write mode
- Database constituent sync: `rows_planned=204`, `rows_written=0`, `missing_definitions=0`, `write_constituents=False`
- Database constituent IDs: none; dry-run/no-write mode
- Database tide prediction sync: `rows_planned=947375`, `rows_deleted=0`, `rows_written=0`, `write_tide_predictions=False`
- Database high/low prediction sync: `rows_planned=8467`, `rows_deleted=0`, `rows_written=0`, `write_high_low_predictions=False`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1992-12-04 01:00:00` to `2100-12-31 23:00:00` (947375 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=160999, full_epoch_rmse_mm=72.87, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=169999, full_epoch_rmse_mm=82.68, plot_window_rows=744
- RECENT_2007-04-30_2026-04-30: constituents=68, hourly_overlap_rows=163537, full_epoch_rmse_mm=74.16, plot_window_rows=744

## 002c
- Station name: Tarawa, Betio
- Station kind: RQ
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station002/netcdf/002c.nc`
- Database sync: `time_series_id=449`, `id_from_source=000002C`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=True`
- Database epoch IDs: `RECENT_1988-01-20_1997-12-31=1801`
- Database datum sync: `rows_planned=13`, `rows_written=0`, `write_datums=False`
- Database datum IDs: none; dry-run/no-write mode
- Database constituent sync: `rows_planned=68`, `rows_written=0`, `missing_definitions=0`, `write_constituents=False`
- Database constituent IDs: none; dry-run/no-write mode
- Database tide prediction sync: `rows_planned=87211`, `rows_deleted=0`, `rows_written=0`, `write_tide_predictions=False`
- Database tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1988-01-20 05:00:00 to 1997-12-31 23:00:00.`
- Database high/low prediction sync: `rows_planned=14043`, `rows_deleted=0`, `rows_written=0`, `write_high_low_predictions=False`
- Database high/low prediction notes: `RQ high/low prediction generated for DB write from date_range_by_time_series_quality: 1988-01-20 05:00:00 to 1997-12-31 23:00:00.`
- Prediction basis epoch: `RECENT_1988-01-20_1997-12-31`
- Prediction scope: `record_span`
- Saved hourly prediction: `hourly_prediction_primary` from `1988-01-20 05:00:00` to `1997-12-31 23:00:00` (87211 rows)
- Saved minute high/low prediction: none
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- RECENT_1988-01-20_1997-12-31: constituents=68, hourly_overlap_rows=87159, full_epoch_rmse_mm=78.35, plot_window_rows=744
