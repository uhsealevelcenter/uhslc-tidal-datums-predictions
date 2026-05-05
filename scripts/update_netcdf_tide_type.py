from __future__ import annotations

import argparse
from pathlib import Path
import sys

import netCDF4
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import compute_tide_type, fetch_fd_hourly, fetch_rq_hourly


def _record_parts(record_id: str, station_kind: str) -> tuple[str, str | None]:
    digits = ''.join(ch for ch in record_id if ch.isdigit())
    station_id = str(int(digits)).zfill(3)
    if station_kind.upper() == 'RQ':
        suffix = record_id[len(digits):]
        if not suffix:
            raise ValueError(f'RQ record lacks version suffix: {record_id}')
        return station_id, suffix.upper()
    return station_id, None


def _to_timestamp(value) -> pd.Timestamp:
    return pd.Timestamp(value).tz_localize(None)


def _load_observations(record_id: str, station_kind: str, cache: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    key = (station_kind.upper(), record_id)
    if key in cache:
        return cache[key]
    station_id, version = _record_parts(record_id, station_kind)
    if station_kind.upper() == 'FD':
        df = fetch_fd_hourly(station_id)
    else:
        assert version is not None
        df = fetch_rq_hourly(station_id, version)
    cache[key] = df[['time', 'sea_level']].copy()
    return cache[key]


def _new_tide_types(path: Path, cache: dict[tuple[str, str], pd.DataFrame]) -> tuple[list[str], list[str], list[str]]:
    with xr.open_dataset(path) as ds:
        record_id = str(ds.attrs['station_id'])
        station_kind = str(ds.attrs['station_kind'])
        epoch_names = [str(value) for value in ds['epoch'].values]
        epoch_starts = [_to_timestamp(value) for value in ds['epoch_start'].values]
        epoch_ends = [_to_timestamp(value) for value in ds['epoch_end'].values]
        old_types = [str(value) for value in ds['tide_type'].values]

    obs = _load_observations(record_id, station_kind, cache)
    new_types = []
    for start, end in zip(epoch_starts, epoch_ends):
        sub = obs[(obs['time'] >= start) & (obs['time'] <= end)][['time', 'sea_level']].copy()
        new_types.append(compute_tide_type(sub))
    return epoch_names, old_types, new_types


def _write_tide_types(path: Path, new_types: list[str]) -> None:
    with netCDF4.Dataset(path, 'a') as ds:
        if 'tide_type' not in ds.variables:
            raise KeyError(f'{path} has no tide_type variable')
        var = ds.variables['tide_type']
        for idx, value in enumerate(new_types):
            var[idx] = value


def main() -> None:
    parser = argparse.ArgumentParser(description='Update existing NetCDF tide_type values from observed ERDDAP hourly data.')
    parser.add_argument('paths', nargs='*', type=Path, help='NetCDF files to update. Defaults to artifacts/**/netcdf/*.nc.')
    parser.add_argument('--write', action='store_true', help='Write updated tide_type values. Without this flag, run as a dry-run.')
    args = parser.parse_args()

    paths = args.paths or sorted(Path('artifacts').glob('**/netcdf/*.nc'))
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    changed = 0

    for path in paths:
        epoch_names, old_types, new_types = _new_tide_types(path, cache)
        file_changed = old_types != new_types
        changed += int(file_changed)
        status = 'CHANGE' if file_changed else 'OK'
        print(f'{status} {path}')
        for epoch_name, old, new in zip(epoch_names, old_types, new_types):
            marker = '->' if old != new else '=='
            print(f'  {epoch_name}: {old} {marker} {new}')
        if args.write and file_changed:
            _write_tide_types(path, new_types)

    mode = 'updated' if args.write else 'would update'
    print(f'{mode} {changed} of {len(paths)} NetCDF files')


if __name__ == '__main__':
    main()
