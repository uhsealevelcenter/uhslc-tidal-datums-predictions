# Station 007 Datums Predictions

Output root: `/home/uhslc/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007`

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

## Prediction auto cleanup
- Current target: `000007B` (`time_series_id=315`)
- Actions planned: `0`
- Write cleanup: `True`
- Tide prediction rows deleted: `0`
- HF tide prediction rows deleted: `0`
- High/low prediction rows deleted: `0`

## 007
- Station name: Malakal
- Station kind: FD
- NetCDF: `/home/uhslc/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007.nc`
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=True`
- Database epoch IDs: `NTDE_2002-2020=1690, NTDE_1983-2001=1691, IPCC-AR6_1995-2014=1692, RECENT_2007-06-30_2026-06-30=1693`
- Database datum sync: `rows_planned=64`, `rows_written=64`, `write_datums=True`
- Database datum IDs for `NTDE_2002-2020`: `dhq=194043, dlq=194044, dtl=194036, gt=194041, hat=194045, hat_time=194048, lat=194046, lat_time=194049, mhhw=194034, mhw=194035, mllw=194040, mlw=194039, mn=194042, msl=194038, mtl=194037, stnd=194047`
- Database datum IDs for `NTDE_1983-2001`: `dhq=194059, dlq=194060, dtl=194052, gt=194057, hat=194061, hat_time=194064, lat=194062, lat_time=194065, mhhw=194050, mhw=194051, mllw=194056, mlw=194055, mn=194058, msl=194054, mtl=194053, stnd=194063`
- Database datum IDs for `IPCC-AR6_1995-2014`: `dhq=194075, dlq=194076, dtl=194068, gt=194073, hat=194077, hat_time=194080, lat=194078, lat_time=194081, mhhw=194066, mhw=194067, mllw=194072, mlw=194071, mn=194074, msl=194070, mtl=194069, stnd=194079`
- Database datum IDs for `RECENT_2007-06-30_2026-06-30`: `dhq=194091, dlq=194092, dtl=194084, gt=194089, hat=194093, hat_time=194096, lat=194094, lat_time=194097, mhhw=194082, mhw=194083, mllw=194088, mlw=194087, mn=194090, msl=194086, mtl=194085, stnd=194095`
- Database constituent sync: `rows_planned=272`, `rows_written=272`, `missing_definitions=68`, `write_constituents=True`
- Missing constituent definitions: `2MK5, 2MK6, 2MN6, 2MS6, 2N2, 2Q1, 2SK5, 2SM6, 3MK7, ALP1, BET1, CHI1, EPS2, ETA2, GAM2, H1, H2, J1, K1, K2, L2, LDA2, M2, M3, M4, M6, M8, MF, MK3, MK4, MKS2, MM, MN4, MO3, MS4, MSF, MSK6, MSM, MSN2, MU2, N2, NO1, NU2, O1, OO1, OQ2, P1, PHI1, PI1, PSI1, Q1, R2, RHO1, S1, S2, S4, SA, SIG1, SK3, SK4, SN4, SO1, SO3, SSA, T2, TAU1, THE1, UPS1`
- Database constituent IDs for `NTDE_2002-2020`: `68 row(s)`
- Database constituent IDs for `NTDE_1983-2001`: `68 row(s)`
- Database constituent IDs for `IPCC-AR6_1995-2014`: `68 row(s)`
- Database constituent IDs for `RECENT_2007-06-30_2026-06-30`: `68 row(s)`
- Database tide prediction sync: `rows_planned=1153785`, `rows_deleted=0`, `rows_written=1153785`, `write_tide_predictions=True`
- Database HF tide prediction sync: `resolution=6-minute`, `rows_planned=1753191`, `rows_deleted=0`, `rows_written=1753191`, `write_hf_tide_predictions=True`
- Database HF tide prediction notes: `HF prediction for NTDE_2002-2020 will use full-precision hourly values and natural cubic-spline interpolation at 6-minute: 2016-01-01 00:00:00 to 2035-12-31 23:00:00.`
- Database high/low prediction sync: `rows_planned=8467`, `rows_deleted=0`, `rows_written=8467`, `write_high_low_predictions=True`
- Database stale RECENT epoch cleanup: `stale_epochs=0`, `epochs_deleted=0`, `tide_rows_deleted=0`, `hf_tide_rows_deleted=0`, `high_low_rows_deleted=0`, `write_cleanup=True`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1969-05-18 15:00:00` to `2100-12-31 23:00:00` (1153785 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=163991, full_epoch_rmse_mm=123.02, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=165292, full_epoch_rmse_mm=128.05, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=173651, full_epoch_rmse_mm=129.12, plot_window_rows=744
- RECENT_2007-06-30_2026-06-30: constituents=68, hourly_overlap_rows=165059, full_epoch_rmse_mm=115.14, plot_window_rows=744

## 007a
- Station name: Malakal
- Station kind: RQ
- NetCDF: `/home/uhslc/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007a.nc`
- Database sync: `time_series_id=372`, `id_from_source=000007A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=True`
- Database epoch IDs: `RECENT_1926-01-01_1939-12-10=1694`
- Database datum sync: `rows_planned=16`, `rows_written=16`, `write_datums=True`
- Database datum IDs for `RECENT_1926-01-01_1939-12-10`: `dhq=194107, dlq=194108, dtl=194100, gt=194105, hat=194109, hat_time=194112, lat=194110, lat_time=194113, mhhw=194098, mhw=194099, mllw=194104, mlw=194103, mn=194106, msl=194102, mtl=194101, stnd=194111`
- Database constituent sync: `rows_planned=68`, `rows_written=68`, `missing_definitions=0`, `write_constituents=True`
- Database constituent IDs for `RECENT_1926-01-01_1939-12-10`: `68 row(s)`
- Database tide prediction sync: `rows_planned=122199`, `rows_deleted=0`, `rows_written=122199`, `write_tide_predictions=True`
- Database tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1926-01-01 01:00:00 to 1939-12-10 15:00:00.`
- Database HF tide prediction sync: `resolution=None`, `rows_planned=0`, `rows_deleted=0`, `rows_written=0`, `write_hf_tide_predictions=True`
- Database HF tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1926-01-01 01:00:00 to 1939-12-10 15:00:00. | No HF overlap for 007a RECENT_1926-01-01_1939-12-10: regular prediction window=1926-01-01 01:00:00 to 1939-12-10 15:00:00; configured HF window=2016-01-01 00:00:00 to 2035-12-31 23:00:00. No HF rows will be inserted.`
- Database high/low prediction sync: `rows_planned=19676`, `rows_deleted=0`, `rows_written=19676`, `write_high_low_predictions=True`
- Database high/low prediction notes: `RQ high/low prediction generated for DB write from date_range_by_time_series_quality: 1926-01-01 01:00:00 to 1939-12-10 15:00:00.`
- Database stale RECENT epoch cleanup: `stale_epochs=0`, `epochs_deleted=0`, `tide_rows_deleted=0`, `hf_tide_rows_deleted=0`, `high_low_rows_deleted=0`, `write_cleanup=True`
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
- NetCDF: `/home/uhslc/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007b.nc`
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=True`
- Database epoch IDs: `NTDE_2002-2020=1695, NTDE_1983-2001=1696, IPCC-AR6_1995-2014=1697, RECENT_2005-12-31_2024-12-31=1698`
- Database datum sync: `rows_planned=64`, `rows_written=64`, `write_datums=True`
- Database datum IDs for `NTDE_2002-2020`: `dhq=194123, dlq=194124, dtl=194116, gt=194121, hat=194125, hat_time=194128, lat=194126, lat_time=194129, mhhw=194114, mhw=194115, mllw=194120, mlw=194119, mn=194122, msl=194118, mtl=194117, stnd=194127`
- Database datum IDs for `NTDE_1983-2001`: `dhq=194139, dlq=194140, dtl=194132, gt=194137, hat=194141, hat_time=194144, lat=194142, lat_time=194145, mhhw=194130, mhw=194131, mllw=194136, mlw=194135, mn=194138, msl=194134, mtl=194133, stnd=194143`
- Database datum IDs for `IPCC-AR6_1995-2014`: `dhq=194155, dlq=194156, dtl=194148, gt=194153, hat=194157, hat_time=194160, lat=194158, lat_time=194161, mhhw=194146, mhw=194147, mllw=194152, mlw=194151, mn=194154, msl=194150, mtl=194149, stnd=194159`
- Database datum IDs for `RECENT_2005-12-31_2024-12-31`: `dhq=194171, dlq=194172, dtl=194164, gt=194169, hat=194173, hat_time=194176, lat=194174, lat_time=194177, mhhw=194162, mhw=194163, mllw=194168, mlw=194167, mn=194170, msl=194166, mtl=194165, stnd=194175`
- Database constituent sync: `rows_planned=272`, `rows_written=272`, `missing_definitions=0`, `write_constituents=True`
- Database constituent IDs for `NTDE_2002-2020`: `68 row(s)`
- Database constituent IDs for `NTDE_1983-2001`: `68 row(s)`
- Database constituent IDs for `IPCC-AR6_1995-2014`: `68 row(s)`
- Database constituent IDs for `RECENT_2005-12-31_2024-12-31`: `68 row(s)`
- Database tide prediction sync: `rows_planned=487593`, `rows_deleted=0`, `rows_written=487593`, `write_tide_predictions=True`
- Database tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1969-05-18 15:00:00 to 2024-12-31 23:00:00.`
- Database HF tide prediction sync: `resolution=6-minute`, `rows_planned=789111`, `rows_deleted=0`, `rows_written=789111`, `write_hf_tide_predictions=True`
- Database HF tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1969-05-18 15:00:00 to 2024-12-31 23:00:00. | HF prediction for NTDE_2002-2020 will use full-precision hourly values and natural cubic-spline interpolation at 6-minute: 2016-01-01 00:00:00 to 2024-12-31 23:00:00.`
- Database high/low prediction sync: `rows_planned=78513`, `rows_deleted=0`, `rows_written=78513`, `write_high_low_predictions=True`
- Database high/low prediction notes: `RQ high/low prediction generated for DB write from date_range_by_time_series_quality: 1969-05-18 15:00:00 to 2024-12-31 23:00:00.`
- Database stale RECENT epoch cleanup: `stale_epochs=0`, `epochs_deleted=0`, `tide_rows_deleted=0`, `hf_tide_rows_deleted=0`, `high_low_rows_deleted=0`, `write_cleanup=True`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1969-05-18 15:00:00` to `2100-12-31 23:00:00` (1153785 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=163991, full_epoch_rmse_mm=123.02, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=165292, full_epoch_rmse_mm=128.05, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=173651, full_epoch_rmse_mm=129.12, plot_window_rows=744
- RECENT_2005-12-31_2024-12-31: constituents=68, hourly_overlap_rows=165109, full_epoch_rmse_mm=117.15, plot_window_rows=744
