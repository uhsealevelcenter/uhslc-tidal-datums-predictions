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

## Prediction auto cleanup
- Current target: `000007B` (`time_series_id=315`)
- Actions planned: `0`
- Write cleanup: `True`
- Tide prediction rows deleted: `0`
- High/low prediction rows deleted: `0`

## 007
- Station name: Malakal
- Station kind: FD
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007.nc`
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=best_available`, `resolution_rule=fd_unversioned_source_resolved_by_database`, `write_epochs=True`
- Database epoch IDs: `NTDE_2002-2020=1838, NTDE_1983-2001=1839, IPCC-AR6_1995-2014=1840, RECENT_2007-05-31_2026-05-31=1916`
- Database datum sync: `rows_planned=52`, `rows_written=52`, `write_datums=True`
- Database datum IDs for `NTDE_2002-2020`: `dhq=235750, dlq=235751, dtl=235743, gt=235748, hat=235752, lat=235753, mhhw=235741, mhw=235742, mllw=235747, mlw=235746, mn=235749, msl=235745, mtl=235744`
- Database datum IDs for `NTDE_1983-2001`: `dhq=235763, dlq=235764, dtl=235756, gt=235761, hat=235765, lat=235766, mhhw=235754, mhw=235755, mllw=235760, mlw=235759, mn=235762, msl=235758, mtl=235757`
- Database datum IDs for `IPCC-AR6_1995-2014`: `dhq=235776, dlq=235777, dtl=235769, gt=235774, hat=235778, lat=235779, mhhw=235767, mhw=235768, mllw=235773, mlw=235772, mn=235775, msl=235771, mtl=235770`
- Database datum IDs for `RECENT_2007-05-31_2026-05-31`: `dhq=236023, dlq=236024, dtl=236016, gt=236021, hat=236025, lat=236026, mhhw=236014, mhw=236015, mllw=236020, mlw=236019, mn=236022, msl=236018, mtl=236017`
- Database constituent sync: `rows_planned=272`, `rows_written=272`, `missing_definitions=0`, `write_constituents=True`
- Database constituent IDs for `NTDE_2002-2020`: `68 row(s)`
- Database constituent IDs for `NTDE_1983-2001`: `68 row(s)`
- Database constituent IDs for `IPCC-AR6_1995-2014`: `68 row(s)`
- Database constituent IDs for `RECENT_2007-05-31_2026-05-31`: `68 row(s)`
- Database tide prediction sync: `rows_planned=1153785`, `rows_deleted=1153785`, `rows_written=1153785`, `write_tide_predictions=True`
- Database high/low prediction sync: `rows_planned=8467`, `rows_deleted=8467`, `rows_written=8467`, `write_high_low_predictions=True`
- Prediction basis epoch: `NTDE_2002-2020`
- Prediction scope: `long_future`
- Saved hourly prediction: `hourly_prediction_primary` from `1969-05-18 15:00:00` to `2100-12-31 23:00:00` (1153785 rows)
- Saved minute high/low prediction: `minute_highlow_time_primary`, `minute_highlow_height_mm_primary`, `minute_highlow_type_primary` from `2025-01-01 00:00:00` to `2030-12-31 23:59:00` (8467 rows)
- Update cycle: 60 months (default)
- Saved epochs/datums/harmonics:
- NTDE_2002-2020: constituents=68, hourly_overlap_rows=164206, full_epoch_rmse_mm=124.10, plot_window_rows=744
- NTDE_1983-2001: constituents=68, hourly_overlap_rows=165292, full_epoch_rmse_mm=128.05, plot_window_rows=744
- IPCC-AR6_1995-2014: constituents=68, hourly_overlap_rows=173651, full_epoch_rmse_mm=129.12, plot_window_rows=744
- RECENT_2007-05-31_2026-05-31: constituents=68, hourly_overlap_rows=165253, full_epoch_rmse_mm=116.54, plot_window_rows=744

## 007a
- Station name: Malakal
- Station kind: RQ
- NetCDF: `/home/nwstg/uhslc-tidal-datums-predictions/artifacts/datums_predictions/station007/netcdf/007a.nc`
- Database sync: `time_series_id=372`, `id_from_source=000007A`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=True`
- Database epoch IDs: `RECENT_1926-01-01_1939-12-10=1842`
- Database datum sync: `rows_planned=13`, `rows_written=13`, `write_datums=True`
- Database datum IDs for `RECENT_1926-01-01_1939-12-10`: `dhq=235802, dlq=235803, dtl=235795, gt=235800, hat=235804, lat=235805, mhhw=235793, mhw=235794, mllw=235799, mlw=235798, mn=235801, msl=235797, mtl=235796`
- Database constituent sync: `rows_planned=68`, `rows_written=68`, `missing_definitions=0`, `write_constituents=True`
- Database constituent IDs for `RECENT_1926-01-01_1939-12-10`: `68 row(s)`
- Database tide prediction sync: `rows_planned=122199`, `rows_deleted=122199`, `rows_written=122199`, `write_tide_predictions=True`
- Database tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1926-01-01 01:00:00 to 1939-12-10 15:00:00.`
- Database high/low prediction sync: `rows_planned=19676`, `rows_deleted=19676`, `rows_written=19676`, `write_high_low_predictions=True`
- Database high/low prediction notes: `RQ high/low prediction generated for DB write from date_range_by_time_series_quality: 1926-01-01 01:00:00 to 1939-12-10 15:00:00.`
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
- Database sync: `time_series_id=315`, `id_from_source=000007B`, `input_basis=research_quality`, `resolution_rule=rq_exact_id_from_source`, `write_epochs=True`
- Database epoch IDs: `NTDE_2002-2020=1843, NTDE_1983-2001=1844, IPCC-AR6_1995-2014=1845, RECENT_1999-12-31_2018-12-31=1846`
- Database datum sync: `rows_planned=52`, `rows_written=52`, `write_datums=True`
- Database datum IDs for `NTDE_2002-2020`: `dhq=235815, dlq=235816, dtl=235808, gt=235813, hat=235817, lat=235818, mhhw=235806, mhw=235807, mllw=235812, mlw=235811, mn=235814, msl=235810, mtl=235809`
- Database datum IDs for `NTDE_1983-2001`: `dhq=235828, dlq=235829, dtl=235821, gt=235826, hat=235830, lat=235831, mhhw=235819, mhw=235820, mllw=235825, mlw=235824, mn=235827, msl=235823, mtl=235822`
- Database datum IDs for `IPCC-AR6_1995-2014`: `dhq=235841, dlq=235842, dtl=235834, gt=235839, hat=235843, lat=235844, mhhw=235832, mhw=235833, mllw=235838, mlw=235837, mn=235840, msl=235836, mtl=235835`
- Database datum IDs for `RECENT_1999-12-31_2018-12-31`: `dhq=235854, dlq=235855, dtl=235847, gt=235852, hat=235856, lat=235857, mhhw=235845, mhw=235846, mllw=235851, mlw=235850, mn=235853, msl=235849, mtl=235848`
- Database constituent sync: `rows_planned=272`, `rows_written=272`, `missing_definitions=0`, `write_constituents=True`
- Database constituent IDs for `NTDE_2002-2020`: `68 row(s)`
- Database constituent IDs for `NTDE_1983-2001`: `68 row(s)`
- Database constituent IDs for `IPCC-AR6_1995-2014`: `68 row(s)`
- Database constituent IDs for `RECENT_1999-12-31_2018-12-31`: `68 row(s)`
- Database tide prediction sync: `rows_planned=487593`, `rows_deleted=487593`, `rows_written=487593`, `write_tide_predictions=True`
- Database tide prediction notes: `RQ hourly prediction bounded by date_range_by_time_series_quality: 1969-05-18 15:00:00 to 2024-12-31 23:00:00.`
- Database high/low prediction sync: `rows_planned=78513`, `rows_deleted=78513`, `rows_written=78513`, `write_high_low_predictions=True`
- Database high/low prediction notes: `RQ high/low prediction generated for DB write from date_range_by_time_series_quality: 1969-05-18 15:00:00 to 2024-12-31 23:00:00.`
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
