import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
import pandas as pd
import xarray as xr

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import replace

from core import SwitchLevel, build_datums_only_dataset, build_netcdf_dataset, build_netcdf_skill_text, build_prediction_save_plan, cap_prediction_end, clean_hourly_dataframe, compute_datums, determine_update_cycle, fetch_station_metadata_index, fit_harmonics, prepare_harmonic_fit_dataframe, get_netcdf_skill_reference, get_rq_metadata_span, get_station_metadata, list_rq_versions, load_harmonic_result, parse_switch_din, predict_from_harmonics, predict_fd_high_low, predict_minute_high_low, extract_daily_high_low, extract_daily_high_low_chunked, saved_prediction_epoch_items, save_harmonic_result, save_netcdf, select_epochs, select_primary_prediction_epoch, select_recent_epoch, strip_harmonic_result

from tidal_config import PREDICTION_POLICY


class TestTidalCore(unittest.TestCase):
    @staticmethod
    def sample_meta_payload():
        return {
            'type': 'FeatureCollection',
            'features': [
                {
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [134.463, 7.33]},
                    'properties': {
                        'uhslc_id': 7,
                        'name': 'Malakal',
                        'country': 'Palau',
                        'fd_span': {'oldest': '1969-05-19', 'latest': '2026-02-28'},
                        'rq_span': {'oldest': '1926-01-01', 'latest': '2018-12-31'},
                        'rq_versions': {
                            'a': {'begin': '1926-01-01', 'end': '1939-12-10'},
                            'b': {'begin': '1983-01-01', 'end': '2018-12-31'},
                        },
                    },
                }
            ],
        }

    @staticmethod
    def sample_switch_din_text():
        return """007MAL       PLAT=07 19.8N LONG=134 27.8E TMZONE=GMT    REF=
  PRS  RAD  RA2  RA3  LEV  LEB
    1    1    1    1   60   60
 1240 4799 -271 -153 1644 1541
    0    0    0    0    0    0
                    Date: 2023-07-04 08:30:00
"""

    @staticmethod
    def synthetic_hourly(start='2002-01-01 00:00:00', end='2002-08-31 23:00:00'):
        time = pd.date_range(start, end, freq='1h')
        hours = np.arange(len(time), dtype=float)
        sea = 1000*np.sin(2*np.pi*hours/12.42) + 250*np.sin(2*np.pi*hours/24.0) + 10*np.random.default_rng(42).normal(size=len(time))
        return pd.DataFrame({'time': time, 'sea_level': sea})

    @staticmethod
    def tide_type_hourly(semidiurnal_amp=0.0, diurnal_amp=0.0, days=90):
        time = pd.date_range('2002-01-01 00:00:00', periods=24 * days, freq='1h')
        hours = np.arange(len(time), dtype=float)
        sea = (
            semidiurnal_amp * np.sin(2 * np.pi * hours / 12.42)
            + diurnal_amp * np.sin(2 * np.pi * hours / 24.84)
        )
        return pd.DataFrame({'time': time, 'sea_level': sea})

    def setUp(self):
        fetch_station_metadata_index.cache_clear()

    def tearDown(self):
        fetch_station_metadata_index.cache_clear()

    def test_clean_dataframe(self):
        df = pd.DataFrame({'time': ['2002-01-01 00:00:00','2002-01-01 00:00:00','2002-01-01 01:00:00'], 'sea_level': [1, -32767, 3]})
        out = clean_hourly_dataframe(df)
        self.assertEqual(len(out), 2)
        self.assertTrue(np.isfinite(out['sea_level'].iloc[-1]))

    def test_select_recent_epoch(self):
        df = self.synthetic_hourly()
        epochs = select_epochs(df)
        self.assertTrue(len(epochs) >= 1)
        self.assertEqual(epochs[0].source, 'recent')
        self.assertEqual(epochs[0].role, 'datum')

    def test_select_recent_epoch_allows_three_months(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-04-15 23:00:00')
        epochs = select_epochs(df)
        self.assertEqual(len(epochs), 1)
        self.assertEqual(epochs[0].source, 'recent')

    def test_select_primary_prediction_epoch_uses_hierarchy(self):
        time = pd.date_range('1983-01-01 00:00:00', '2020-12-31 23:00:00', freq='1h')
        df = pd.DataFrame({'time': time, 'sea_level': 100.0})
        epochs = select_epochs(df)
        self.assertEqual([e.name for e in epochs[:2]], ['NTDE_2002-2020', 'NTDE_1983-2001'])
        self.assertEqual(select_primary_prediction_epoch(epochs).name, 'NTDE_2002-2020')
        self.assertEqual(epochs[-1].source, 'recent')

    def test_recent_is_selected_with_fixed_epochs(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2023-12-31 23:00:00')
        epochs = select_epochs(df)
        self.assertIn('NTDE_2002-2020', [e.name for e in epochs])
        self.assertTrue(any(e.source == 'recent' for e in epochs))
        self.assertEqual(select_primary_prediction_epoch(epochs).name, 'NTDE_2002-2020')
        recent = select_recent_epoch(clean_hourly_dataframe(df))
        self.assertIsNotNone(recent)
        assert recent is not None
        self.assertEqual(recent.end, pd.Timestamp('2023-12-31 23:00:00'))

    def test_select_prediction_epoch_when_primary_lacks_annual_completion(self):
        df = self.synthetic_hourly(start='1982-01-01 00:00:00', end='2000-12-31 23:00:00')
        epochs = select_epochs(df)
        self.assertEqual([e.name for e in epochs], ['NTDE_1983-2001', 'PRED_1982_2000'])
        self.assertEqual([e.source for e in epochs], ['primary', 'prediction'])
        self.assertEqual([e.role for e in epochs], ['datum', 'harmonic_prediction'])
        self.assertGreaterEqual(epochs[0].completion_fraction, 0.75)
        self.assertEqual(epochs[1].start, pd.Timestamp('1982-01-01 00:00:00'))
        self.assertEqual(epochs[1].end, pd.Timestamp('2000-12-31 23:00:00'))

    def test_prepare_harmonic_fit_dataframe_defines_actual_fit_window(self):
        df = pd.DataFrame(
            {
                "time": pd.date_range("2002-01-01 00:00:00", periods=6, freq="1h"),
                "sea_level": [np.nan, 10.0, 11.0, -32767, 12.0, np.nan],
            }
        )
    
        fit_df = prepare_harmonic_fit_dataframe(df)
    
        self.assertEqual(len(fit_df), 3)
        self.assertEqual(fit_df["time"].min(), pd.Timestamp("2002-01-01 01:00:00"))
        self.assertEqual(fit_df["time"].max(), pd.Timestamp("2002-01-01 04:00:00"))
        self.assertEqual(fit_df["sea_level"].tolist(), [10.0, 11.0, 12.0])

    def test_compute_datums(self):
        df = self.synthetic_hourly()
        pred = pd.DataFrame({
            'time': df['time'],
            'prediction_mm': np.full(len(df), 123.0),
        })
        dat = compute_datums(df, epoch_prediction=pred)
        self.assertTrue(np.isfinite(dat.MSL))
        self.assertEqual(dat.HAT, 123.0)
        self.assertEqual(dat.LAT, 123.0)
        self.assertAlmostEqual(dat.p90_low, float(np.nanpercentile(df['sea_level'].to_numpy(dtype=float), 10)))
        self.assertAlmostEqual(dat.p99_high, float(np.nanpercentile(df['sea_level'].to_numpy(dtype=float), 99)))
        self.assertIn(dat.tide_type, ['Diurnal', 'Semidiurnal', 'Mixed Semidiurnal', 'Unknown'])

    def test_tide_type_diurnal(self):
        dat = compute_datums(self.tide_type_hourly(diurnal_amp=1000.0))
        self.assertEqual(dat.tide_type, 'Diurnal')

    def test_tide_type_semidiurnal(self):
        dat = compute_datums(self.tide_type_hourly(semidiurnal_amp=1000.0))
        self.assertEqual(dat.tide_type, 'Semidiurnal')

    def test_tide_type_mixed_semidiurnal(self):
        dat = compute_datums(self.tide_type_hourly(semidiurnal_amp=1000.0, diurnal_amp=350.0))
        self.assertEqual(dat.tide_type, 'Mixed Semidiurnal')

    def test_harmonics_and_prediction(self):
        df = self.synthetic_hourly()
        hr = fit_harmonics(df, latitude=21.3)
        self.assertTrue(len(hr.constituent) > 0)
        pred = predict_from_harmonics(hr, pd.Timestamp('2002-04-01 00:00:00'), pd.Timestamp('2002-04-03 23:00:00'))
        self.assertEqual(len(pred), 72)
        self.assertTrue(np.isfinite(pred['prediction_mm']).all())

    def test_predict_from_harmonics_chunked_matches_direct_hourly(self):
        df = self.synthetic_hourly(
            start="2002-01-01 00:00:00",
            end="2002-03-31 23:00:00",
        )
        hr = fit_harmonics(df, latitude=21.3)
    
        start = pd.Timestamp("2002-02-01 00:00:00")
        end = pd.Timestamp("2002-02-10 23:00:00")
    
        direct = predict_from_harmonics(
            hr,
            start,
            end,
            freq="1h",
            max_points_per_chunk=0,
        )
    
        chunked = predict_from_harmonics(
            hr,
            start,
            end,
            freq="1h",
            max_points_per_chunk=24,
        )
    
        self.assertEqual(len(chunked), len(direct))
    
        np.testing.assert_array_equal(
            chunked["time"].to_numpy(),
            direct["time"].to_numpy(),
        )
    
        np.testing.assert_allclose(
            chunked["prediction_mm"].to_numpy(),
            direct["prediction_mm"].to_numpy(),
            rtol=1e-10,
            atol=1e-10,
        )
    
    
    def test_predict_from_harmonics_chunked_matches_direct_minute(self):
        df = self.synthetic_hourly(
            start="2002-01-01 00:00:00",
            end="2002-03-31 23:00:00",
        )
        hr = fit_harmonics(df, latitude=21.3)
    
        start = pd.Timestamp("2002-02-01 00:00:00")
        end = pd.Timestamp("2002-02-02 23:59:00")
    
        direct = predict_from_harmonics(
            hr,
            start,
            end,
            freq="1min",
            max_points_per_chunk=0,
        )
    
        chunked = predict_from_harmonics(
            hr,
            start,
            end,
            freq="1min",
            max_points_per_chunk=500,
        )
    
        self.assertEqual(len(chunked), len(direct))
    
        np.testing.assert_array_equal(
            chunked["time"].to_numpy(),
            direct["time"].to_numpy(),
        )
    
        np.testing.assert_allclose(
            chunked["prediction_mm"].to_numpy(),
            direct["prediction_mm"].to_numpy(),
            rtol=1e-10,
            atol=1e-10,
        )

    def test_harmonic_artifact_roundtrip(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-03-31 23:00:00')
        hr = fit_harmonics(df, latitude=21.3)
        start = pd.Timestamp('2002-02-01 00:00:00')
        end = pd.Timestamp('2002-02-05 23:00:00')
        direct = predict_from_harmonics(hr, start, end)
        summary = strip_harmonic_result(hr)
        self.assertIsNone(summary.coef)
        with tempfile.TemporaryDirectory() as td:
            paths = save_harmonic_result(hr, str(Path(td) / 'harmonics.pkl'), metadata={'station_id': '001'})
            self.assertTrue(Path(paths['pickle']).exists())
            self.assertTrue(Path(paths['json']).exists())
            reloaded = load_harmonic_result(paths['pickle'])
            self.assertEqual(reloaded.constituent, hr.constituent)
            roundtrip = predict_from_harmonics(reloaded, start, end)
            np.testing.assert_allclose(roundtrip['prediction_mm'].to_numpy(), direct['prediction_mm'].to_numpy())

    def test_cap_prediction_end(self):
        self.assertEqual(cap_prediction_end(pd.Timestamp('2030-01-01 00:00:00')), pd.Timestamp('2030-01-01 00:00:00'))
        self.assertEqual(cap_prediction_end(pd.Timestamp('2101-01-01 00:00:00')), pd.Timestamp('2100-12-31 23:00:00'))

    @patch('core._load_json_url')
    def test_station_metadata_from_live_geojson(self, mock_load_json_url):
        mock_load_json_url.return_value = self.sample_meta_payload()
        meta = get_station_metadata('007')
        self.assertEqual(meta.station_id, '007')
        self.assertEqual(meta.name, 'Malakal')
        self.assertAlmostEqual(meta.latitude, 7.33)
        self.assertAlmostEqual(meta.longitude, 134.463)
        self.assertEqual(list_rq_versions(['007'])['007'], ['A', 'B'])
        start, end = get_rq_metadata_span('007', 'b')
        self.assertEqual(start, pd.Timestamp('1983-01-01 00:00:00'))
        self.assertEqual(end, pd.Timestamp('2018-12-31 00:00:00'))

    def test_parse_switch_din(self):
        parsed = parse_switch_din(self.sample_switch_din_text())
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.station_id, '007')
        self.assertEqual(parsed.LEV, 1644.0)
        self.assertEqual(parsed.LEVB, 1541.0)
        self.assertEqual(parsed.Date, '2023-07-04 08:30:00')

    def test_extract_daily_high_low(self):
        time = pd.date_range('2023-01-01 00:00:00', '2023-01-03 23:59:00', freq='1min')
        minutes = np.arange(len(time), dtype=float)
        pred = 1000*np.sin(2*np.pi*minutes/(12.42*60))
        df = pd.DataFrame({'time': time, 'prediction_mm': pred})
        hl = extract_daily_high_low(df)
        self.assertTrue(len(hl) >= 6)
        self.assertTrue(set(hl['type'].unique()).issubset({'H','L'}))

    def test_extract_daily_high_low_chunked(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-03-31 23:00:00')
        hr = fit_harmonics(df, latitude=21.3)
        start = pd.Timestamp('2002-02-01 00:00:00')
        end = pd.Timestamp('2002-02-05 23:59:00')
        direct = extract_daily_high_low(predict_from_harmonics(hr, start, end, freq='1min'))
        chunked = extract_daily_high_low_chunked(hr, start, end, chunk_days=2)
        self.assertTrue(len(chunked) >= 8)
        self.assertEqual(set(chunked['type'].unique()), {'H', 'L'})
        self.assertEqual(set(chunked['time'].dt.date.unique()), set(direct['time'].dt.date.unique()))

    def test_predict_fd_high_low(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-03-31 23:00:00')
        hr = fit_harmonics(df, latitude=21.3)
        hl = predict_fd_high_low(hr, end=pd.Timestamp('2025-01-03 23:59:00'), chunk_days=2)
        self.assertTrue(len(hl) >= 8)
        self.assertEqual(set(hl['type'].unique()), {'H', 'L'})
        self.assertTrue((hl['time'] >= pd.Timestamp('2025-01-01 00:00:00')).all())
        self.assertTrue((hl['time'] <= pd.Timestamp('2025-01-03 23:59:00')).all())

    def test_predict_fd_high_low_caps_to_last_minute_of_2030(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-03-31 23:00:00')
        hr = fit_harmonics(df, latitude=21.3)
        hl = predict_fd_high_low(hr)
        self.assertTrue((hl['time'] >= pd.Timestamp('2025-01-01 00:00:00')).all())
        self.assertTrue((hl['time'] <= pd.Timestamp('2030-12-31 23:59:00')).all())

    def test_predict_minute_high_low_accepts_record_span(self):
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-03-31 23:00:00')
        hr = fit_harmonics(df, latitude=21.3)
        hl = predict_minute_high_low(hr, start=pd.Timestamp('2002-02-01 00:00:00'), end=pd.Timestamp('2002-02-03 23:59:00'), chunk_days=2)
        self.assertTrue((hl['time'] >= pd.Timestamp('2002-02-01 00:00:00')).all())
        self.assertTrue((hl['time'] <= pd.Timestamp('2002-02-03 23:59:00')).all())

    @patch('core._load_json_url')
    def test_prediction_save_plan_fd_long_future(self, mock_load_json_url):
        mock_load_json_url.return_value = self.sample_meta_payload()
        df = self.synthetic_hourly(start='2002-01-01 00:00:00', end='2002-04-15 23:00:00')
        epochs = select_epochs(df)
        plan = build_prediction_save_plan(df, epochs, 'FD', station_id='007', runtime=pd.Timestamp('2026-05-01'))
        self.assertEqual(plan.hourly_start, pd.Timestamp('2002-01-01 00:00:00'))
        self.assertEqual(plan.hourly_end, pd.Timestamp('2100-12-31 23:00:00'))
        self.assertEqual(plan.minute_start, pd.Timestamp('2025-01-01 00:00:00'))
        self.assertEqual(plan.minute_end, pd.Timestamp('2030-12-31 23:59:00'))
        self.assertEqual(plan.prediction_scope, 'long_future')

    @patch('core._load_json_url')
    def test_prediction_save_plan_older_rq_record_span(self, mock_load_json_url):
        mock_load_json_url.return_value = self.sample_meta_payload()
        df = self.synthetic_hourly(start='1926-01-01 00:00:00', end='1926-04-15 23:00:00')
        epochs = select_epochs(df)
        plan = build_prediction_save_plan(df, epochs, 'RQ', station_id='007', version='A', runtime=pd.Timestamp('2026-05-01'))
        self.assertEqual(plan.hourly_start, pd.Timestamp('1926-01-01 00:00:00'))
        self.assertEqual(plan.hourly_end, pd.Timestamp('1926-04-15 23:00:00'))
        self.assertEqual(plan.minute_start, pd.Timestamp('1926-01-01 00:00:00'))
        self.assertEqual(plan.minute_end, pd.Timestamp('1926-04-15 23:00:00'))
        self.assertEqual(plan.prediction_scope, 'record_span')
        self.assertFalse(plan.save_minute_high_low)

    def test_saved_prediction_epoch_items_default_basis_only(self):
        df = self.synthetic_hourly(
            start="2002-01-01 00:00:00",
            end="2020-12-31 23:00:00",
        )
        epochs = select_epochs(df)
        plan = build_prediction_save_plan(
            df,
            epochs,
            "FD",
            station_id="007",
            runtime=pd.Timestamp("2026-05-01"),
        )

        items = saved_prediction_epoch_items(epochs, plan)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "primary")
        self.assertEqual(items[0][1].name, plan.basis_epoch)


    def test_saved_prediction_epoch_items_all_epochs_flag(self):
        df = self.synthetic_hourly(
            start="2002-01-01 00:00:00",
            end="2020-12-31 23:00:00",
        )
        epochs = select_epochs(df)
        plan = build_prediction_save_plan(
            df,
            epochs,
            "FD",
            station_id="007",
            runtime=pd.Timestamp("2026-05-01"),
        )
        policy = replace(PREDICTION_POLICY, save_predictions_for_all_epochs=True)

        items = saved_prediction_epoch_items(epochs, plan, policy=policy)

        self.assertEqual([ep.name for _key, ep in items], [ep.name for ep in epochs])
        self.assertEqual(len({key for key, _ep in items}), len(items))
        self.assertIn("NTDE_2002_2020", {key for key, _ep in items})

    def test_determine_update_cycle_short_recent_record(self):
        df = self.synthetic_hourly(start='2025-01-01 00:00:00', end='2025-04-15 23:00:00')
        epochs = select_epochs(df)
        months, reason = determine_update_cycle(epochs, pd.Timestamp('2025-04-15 23:00:00'))
        self.assertEqual(months, 3)
        self.assertEqual(reason, 'short_epoch_recent_record')

    def test_netcdf_write(self):
        df = self.synthetic_hourly()
        epochs = select_epochs(df)
        ep = epochs[0]
        hr = fit_harmonics(df, latitude=21.3)
        pred = predict_from_harmonics(hr, ep.start, ep.end)
        dat = compute_datums(df, epoch_prediction=pred)
        switch_levels = SwitchLevel(station_id='001', LEV=1644.0, LEVB=1541.0, Date='2023-07-04 08:30:00')
        pred.attrs["epoch_name"] = ep.name
        ds = build_netcdf_dataset('001', 'Test Station', 'RQ', epochs, {ep.name: dat}, {ep.name: hr}, {ep.name: pred}, switch_levels=switch_levels)
        self.assertIn('LEV', ds.variables)
        self.assertIn('epoch_role', ds.variables)
        self.assertEqual(ds['epoch_role'].isel(epoch=0).item(), 'datum')
        self.assertNotIn('skill', ds.variables)
        self.assertEqual(ds.attrs['skill_format'], 'open-skill-markdown')
        self.assertEqual(ds.attrs['skill_name'], 'uhslc-tidal-datums-predictions')
        self.assertIn('Recreate UHSLC tidal datums', ds.attrs['skill_description'])
        self.assertEqual(ds.attrs['skill_local_path'], 'artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md')
        self.assertIn('githubusercontent.com', ds.attrs['skill_remote_url'])
        self.assertNotIn('skill_sha256', ds.attrs)
        self.assertEqual(float(ds['LEV'].isel(epoch=0).item()), 1644.0)
        self.assertIn(f"hourly_prediction_{ep.name}", ds.variables)
        self.assertEqual(ds[f"hourly_prediction_{ep.name}"].attrs["epoch_name"], ep.name)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'test.nc'
            save_netcdf(ds, str(path))
            self.assertTrue(path.exists())
            reopened = xr.open_dataset(path)
            self.assertEqual(reopened.attrs['station_id'], '001')
            self.assertEqual(reopened.attrs['skill_local_path'], 'artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md')
            self.assertNotIn('skill', reopened.variables)
            reopened.close()

    def test_datums_only_netcdf_write(self):
        df = self.synthetic_hourly()
        epochs = select_epochs(df)
        ep = epochs[0]
        hr = fit_harmonics(df, latitude=21.3)
        pred = predict_from_harmonics(hr, ep.start, ep.end)
        dat = compute_datums(df, epoch_prediction=pred)
        switch_levels = SwitchLevel(station_id='001', LEV=1644.0, LEVB=None, Date='2023-07-04 08:30:00')
        ds = build_datums_only_dataset('001', 'Test Station', 'RQ', epochs, {ep.name: dat}, switch_levels=switch_levels)
        self.assertEqual(ds.attrs['content'], 'datums_only')
        self.assertEqual(str(ds['MHHW'].dtype), 'int32')
        self.assertEqual(float(ds['LEV'].isel(epoch=0).item()), 1644.0)
        self.assertNotIn('skill', ds.variables)
        self.assertIn('LEV', build_netcdf_skill_text())
        self.assertEqual(ds.attrs['skill_name'], 'uhslc-tidal-datums-predictions')
        self.assertNotIn('harmonic_constituent', ds.variables)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'test_datums_only.nc'
            save_netcdf(ds, str(path))
            self.assertTrue(path.exists())

    def test_skill_text_format(self):
        skill = build_netcdf_skill_text()
        self.assertTrue(skill.startswith('---\nname: uhslc-tidal-datums-predictions'))
        self.assertIn('## Epoch Selection', skill)
        self.assertIn('## Edge Cases', skill)
        self.assertIn('1982-2000', skill)
        ref = get_netcdf_skill_reference()
        self.assertEqual(ref.name, 'uhslc-tidal-datums-predictions')
        self.assertEqual(ref.local_path, 'artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md')


if __name__ == '__main__':
    unittest.main()
