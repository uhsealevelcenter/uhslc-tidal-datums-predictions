import unittest

import numpy as np
import pandas as pd

from hf_tide_predictions import (
    build_hourly_prediction_spline,
    hf_prediction_row_count,
    iter_hf_prediction_chunks,
    minute_resolution_timedelta,
)


class HfTidePredictionTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2016-01-01", periods=49, freq="1h")
        hours = np.arange(len(times), dtype=float)
        self.hourly = pd.DataFrame(
            {
                "time": times,
                "prediction_mm": 500.0 * np.sin(hours * np.pi / 6.0) + hours / 10.0,
            }
        )

    def test_minute_resolution_timedelta(self):
        self.assertEqual(minute_resolution_timedelta("minute"), pd.Timedelta(minutes=1))
        self.assertEqual(minute_resolution_timedelta("5-minute"), pd.Timedelta(minutes=5))
        self.assertEqual(minute_resolution_timedelta("15 min"), pd.Timedelta(minutes=15))

        with self.assertRaises(ValueError):
            minute_resolution_timedelta("hourly")

    def test_build_hourly_prediction_spline_rejects_gap(self):
        broken = self.hourly.drop(index=10).reset_index(drop=True)
        with self.assertRaisesRegex(ValueError, "complete 1-hour grid"):
            build_hourly_prediction_spline(broken)

    def test_chunked_interpolation_uses_full_precision_and_matches_row_count(self):
        start = pd.Timestamp("2016-01-01 06:00:00")
        end = pd.Timestamp("2016-01-02 18:00:00")
        interval = pd.Timedelta(minutes=5)

        chunks = list(
            iter_hf_prediction_chunks(
                self.hourly,
                start=start,
                end=end,
                interval=interval,
                chunk_days=1,
                value_decimal_places=4,
            )
        )
        result = pd.concat(chunks, ignore_index=True)

        self.assertEqual(
            len(result),
            hf_prediction_row_count(start, end, interval),
        )
        self.assertEqual(result["time"].iloc[0], start)
        self.assertEqual(result["time"].iloc[-1], end)
        self.assertFalse(result["time"].duplicated().any())
        self.assertTrue(np.isfinite(result["value"]).all())

        hourly_result = result[result["time"].isin(self.hourly["time"])]
        source = self.hourly[self.hourly["time"].isin(hourly_result["time"])]
        expected = np.round(source["prediction_mm"].to_numpy() * 0.001, 4)
        np.testing.assert_allclose(hourly_result["value"].to_numpy(), expected)

    def test_interpolation_rejects_extrapolation(self):
        with self.assertRaisesRegex(ValueError, "contained in the hourly source"):
            list(
                iter_hf_prediction_chunks(
                    self.hourly,
                    start=pd.Timestamp("2015-12-31 23:55:00"),
                    end=pd.Timestamp("2016-01-01 01:00:00"),
                    interval=pd.Timedelta(minutes=5),
                    chunk_days=1,
                )
            )


if __name__ == "__main__":
    unittest.main()
