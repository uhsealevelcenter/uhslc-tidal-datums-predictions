from __future__ import annotations

import copy
from functools import lru_cache
from dataclasses import dataclass
from typing import List, Dict, Tuple
import io
import json
from pathlib import Path
import pickle
import re
import time

import numpy as np
import pandas as pd
import xarray as xr
import requests
import yaml
from scipy.signal import find_peaks
from utide import solve, reconstruct

MISSING_VALUE = -32767
ERDDAP_BASE = 'https://uhslc.soest.hawaii.edu/erddap/tabledap'
META_GEOJSON_URL = 'https://uhslc.soest.hawaii.edu/data/meta.geojson'
RQ_META_INDEX = 'https://uhslc.soest.hawaii.edu/rqds/metadata_yaml/'
DIN_INDEX_URL = 'https://uhslc.soest.hawaii.edu/mwidlans/dev/metadata/din/'
SWITCH_LEVELS_CSV = Path(__file__).resolve().parent / 'data' / 'switch_levels.csv'
SKILL_RELATIVE_PATH = Path('artifacts') / 'skills' / 'uhslc-tidal-datums-predictions' / 'SKILL.md'
SKILL_PATH = Path(__file__).resolve().parent / SKILL_RELATIVE_PATH
SKILL_REMOTE_URL = 'https://raw.githubusercontent.com/uhsealevelcenter/uhslc-tidal-datums-predictions/skills/artifacts/skills/uhslc-tidal-datums-predictions/SKILL.md'
PRIMARY_EPOCHS = [
    ("NTDE_1983-2001", pd.Timestamp("1983-01-01 00:00:00"), pd.Timestamp("2001-12-31 23:00:00")),
    ("NTDE_2002-2020", pd.Timestamp("2002-01-01 00:00:00"), pd.Timestamp("2020-12-31 23:00:00")),
    ("IPCC-AR6_1995-2014", pd.Timestamp("1995-01-01 00:00:00"), pd.Timestamp("2014-12-31 23:00:00")),
]
MAX_PREDICTION_END = pd.Timestamp("2035-12-31 23:00:00")
MAX_MINUTE_PREDICTION_START = pd.Timestamp("2025-01-01 00:00:00")
MAX_MINUTE_PREDICTION_END = pd.Timestamp("2030-12-31 23:59:00")
TIDE_TYPE_EQUALITY_FRACTION = 0.10

@dataclass
class Epoch:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp
    source: str
    completion_fraction: float
    n_expected: int
    n_valid: int
    role: str = 'datum'

@dataclass
class HarmonicResult:
    constituent: List[str]
    amplitude_mm: List[float]
    phase_deg: List[float]
    mean_mm: float
    slope_mm_per_day: float
    coef: object | None = None

@dataclass
class DatumResult:
    tide_type: str
    MHHW: float
    MHW: float
    DTL: float
    MTL: float
    MSL: float
    MLW: float
    MLLW: float
    GT: float
    MN: float
    DHQ: float
    DLQ: float
    HAT: float
    LAT: float
    p90_low: float
    p95_low: float
    p99_low: float
    p90_high: float
    p95_high: float
    p99_high: float


@dataclass(frozen=True)
class SwitchLevel:
    station_id: str
    LEV: float | None
    LEVB: float | None
    Date: str | None


@dataclass(frozen=True)
class StationMetadata:
    station_id: str
    name: str
    country: str | None
    latitude: float
    longitude: float
    fd_span: dict | None
    rq_span: dict | None
    rq_versions: dict


@dataclass(frozen=True)
class SkillReference:
    name: str
    description: str
    local_path: str
    remote_url: str
    format: str = 'open-skill-markdown'


def build_netcdf_skill_text() -> str:
    return SKILL_PATH.read_text(encoding='utf-8')


def _parse_skill_frontmatter(skill_text: str) -> dict:
    if not skill_text.startswith('---\n'):
        raise ValueError(f'Skill file is missing YAML frontmatter: {SKILL_RELATIVE_PATH}')
    parts = skill_text.split('---', 2)
    if len(parts) < 3:
        raise ValueError(f'Skill file has malformed YAML frontmatter: {SKILL_RELATIVE_PATH}')
    metadata = yaml.safe_load(parts[1]) or {}
    if not metadata.get('name') or not metadata.get('description'):
        raise ValueError(f'Skill frontmatter requires name and description: {SKILL_RELATIVE_PATH}')
    return metadata


@lru_cache(maxsize=1)
def get_netcdf_skill_reference() -> SkillReference:
    skill_text = build_netcdf_skill_text()
    metadata = _parse_skill_frontmatter(skill_text)
    return SkillReference(
        name=str(metadata['name']),
        description=str(metadata['description']),
        local_path=SKILL_RELATIVE_PATH.as_posix(),
        remote_url=SKILL_REMOTE_URL,
    )


def cap_prediction_end(end: pd.Timestamp) -> pd.Timestamp:
    return min(pd.Timestamp(end), MAX_PREDICTION_END)


def snap_to_hour(times: pd.Series) -> pd.Series:
    t = pd.to_datetime(times, utc=True).dt.tz_localize(None)
    rounded = t.dt.round('h')
    delta = (t - rounded).abs()
    ok = delta <= pd.Timedelta(minutes=5)
    out = t.copy()
    out.loc[ok] = rounded.loc[ok]
    return out


def strip_erddap_units_row(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if len(out) and 'time' in out.columns and str(out.iloc[0]['time']) == 'UTC':
        out = out.iloc[1:].copy()
    return out.reset_index(drop=True)


def load_erddap_csv(url: str) -> pd.DataFrame:
    last_error = None
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=240)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise

        if r.ok and not r.text.startswith('Error {'):
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [re.sub(r'\s*\(.*?\)\s*$', '', str(c)).strip() for c in df.columns]
            return strip_erddap_units_row(df)

        last_error = RuntimeError(r.text[:1200])
        retryable = r.status_code in {429, 500, 502, 503, 504} or 'Service Unavailable' in r.text
        if retryable and attempt < 3:
            time.sleep(2 * (attempt + 1))
            continue
        raise last_error

    if last_error is not None:
        raise last_error
    raise RuntimeError('Unexpected ERDDAP load failure.')


def _load_json_url(url: str, timeout: int = 240) -> dict:
    last_error = None
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            break
    raise RuntimeError(f'Failed to load JSON from {url}') from last_error


def _load_text_url(url: str, timeout: int = 240) -> str:
    last_error = None
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            break
    raise RuntimeError(f'Failed to load text from {url}') from last_error


def parse_switch_din(text: str) -> SwitchLevel | None:
    lines = text.splitlines()
    if not lines:
        return None
    station_match = re.match(r'^\s*(\d{3})', lines[0])
    if station_match is None:
        return None
    station_id = station_match.group(1)

    header_idx = None
    header_tokens: list[str] = []
    for idx, line in enumerate(lines[1:], start=1):
        tokens = line.split()
        if not tokens:
            continue
        if any(tok in {'LEV', 'LEB'} for tok in tokens):
            header_idx = idx
            header_tokens = tokens
            break
        if idx > 8:
            break
    if header_idx is None:
        return None

    numeric_rows: list[list[str]] = []
    for line in lines[header_idx + 1:]:
        tokens = line.split()
        if not tokens:
            continue
        if re.match(r'^Date:', tokens[0], re.IGNORECASE):
            break
        if len(tokens) >= len(header_tokens) and all(re.match(r'^-?\d+$', tok) for tok in tokens[:len(header_tokens)]):
            numeric_rows.append(tokens[:len(header_tokens)])
            if len(numeric_rows) == 3:
                break
        elif numeric_rows:
            break
    if len(numeric_rows) < 2:
        return None

    values = numeric_rows[1]
    lev = None
    levb = None
    for idx, token in enumerate(header_tokens):
        if token == 'LEV':
            lev = float(values[idx])
        elif token == 'LEB':
            levb = float(values[idx])

    date_value = None
    for line in lines[header_idx + 1:header_idx + 12]:
        match = re.search(r'Date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+[0-9]{2}:[0-9]{2}:[0-9]{2})?)', line)
        if match:
            date_value = match.group(1).strip()
            break

    if lev is None and levb is None:
        return None
    return SwitchLevel(station_id=station_id, LEV=lev, LEVB=levb, Date=date_value)


def fetch_switch_din_filenames() -> list[str]:
    html = _load_text_url(DIN_INDEX_URL, timeout=120)
    names = sorted(set(re.findall(r'href="([^"]+\.din)"', html, flags=re.IGNORECASE)))
    return [name for name in names if name.lower().endswith('.din')]


def build_switch_levels_dataframe() -> pd.DataFrame:
    rows = []
    for name in fetch_switch_din_filenames():
        try:
            text = _load_text_url(f'{DIN_INDEX_URL}{name}', timeout=120)
            parsed = parse_switch_din(text)
        except RuntimeError:
            continue
        if parsed is None:
            continue
        rows.append(
            {
                'UHSLC_ID': parsed.station_id,
                'LEV': parsed.LEV,
                'LEVB': parsed.LEVB,
                'Date': parsed.Date,
            }
        )
    if not rows:
        return pd.DataFrame(columns=['UHSLC_ID', 'LEV', 'LEVB', 'Date'])
    df = pd.DataFrame(rows)
    df['UHSLC_ID'] = df['UHSLC_ID'].astype(str).str.zfill(3)
    df = df.sort_values('UHSLC_ID').drop_duplicates('UHSLC_ID', keep='first').reset_index(drop=True)
    return df[['UHSLC_ID', 'LEV', 'LEVB', 'Date']]


def ensure_switch_levels_csv(path: Path = SWITCH_LEVELS_CSV, max_age_hours: int = 24 * 30, force: bool = False) -> Path:
    path = Path(path)
    refresh = force or (not path.exists())
    if not refresh:
        age = pd.Timestamp.utcnow().timestamp() - path.stat().st_mtime
        refresh = age > (max_age_hours * 3600)
    if refresh:
        df = build_switch_levels_dataframe()
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return path


def load_switch_levels(path: Path = SWITCH_LEVELS_CSV, max_age_hours: int = 24 * 30, force_refresh: bool = False) -> pd.DataFrame:
    csv_path = ensure_switch_levels_csv(path=path, max_age_hours=max_age_hours, force=force_refresh)
    if not csv_path.exists():
        return pd.DataFrame(columns=['UHSLC_ID', 'LEV', 'LEVB', 'Date'])
    df = pd.read_csv(csv_path, dtype={'UHSLC_ID': str})
    if 'UHSLC_ID' not in df.columns:
        return pd.DataFrame(columns=['UHSLC_ID', 'LEV', 'LEVB', 'Date'])
    if 'SW1' in df.columns and 'LEV' not in df.columns:
        df = df.rename(columns={'SW1': 'LEV'})
    if 'SW2' in df.columns and 'LEVB' not in df.columns:
        df = df.rename(columns={'SW2': 'LEVB'})
    df['UHSLC_ID'] = df['UHSLC_ID'].astype(str).str.zfill(3)
    for field in ['LEV', 'LEVB']:
        if field in df.columns:
            df[field] = pd.to_numeric(df[field], errors='coerce')
        else:
            df[field] = np.nan
    if 'Date' not in df.columns:
        df['Date'] = None
    return df[['UHSLC_ID', 'LEV', 'LEVB', 'Date']]


def get_station_switch_levels(station_id: str, path: Path = SWITCH_LEVELS_CSV, max_age_hours: int = 24 * 30, force_refresh: bool = False) -> SwitchLevel | None:
    sid = str(int(station_id)).zfill(3)
    df = load_switch_levels(path=path, max_age_hours=max_age_hours, force_refresh=force_refresh)
    match = df[df['UHSLC_ID'] == sid]
    if match.empty:
        return None
    row = match.iloc[0]
    lev = row['LEV'] if pd.notna(row['LEV']) else None
    levb = row['LEVB'] if pd.notna(row['LEVB']) else None
    date_value = None if pd.isna(row['Date']) else str(row['Date'])
    return SwitchLevel(station_id=sid, LEV=lev, LEVB=levb, Date=date_value)


@lru_cache(maxsize=1)
def fetch_station_metadata_index() -> dict[str, StationMetadata]:
    payload = _load_json_url(META_GEOJSON_URL, timeout=120)
    out: dict[str, StationMetadata] = {}
    for feature in payload.get('features', []):
        props = feature.get('properties', {}) or {}
        coords = (feature.get('geometry') or {}).get('coordinates') or [None, None]
        station_id = props.get('uhslc_id')
        name = props.get('name')
        lat = coords[1] if len(coords) > 1 else None
        lon = coords[0] if len(coords) > 0 else None
        if station_id is None or name is None or lat is None or lon is None:
            continue
        rq_versions = props.get('rq_versions') or {}
        out[str(int(station_id)).zfill(3)] = StationMetadata(
            station_id=str(int(station_id)).zfill(3),
            name=str(name),
            country=props.get('country'),
            latitude=float(lat),
            longitude=float(lon),
            fd_span=props.get('fd_span'),
            rq_span=props.get('rq_span'),
            rq_versions=dict(rq_versions),
        )
    return out


def get_station_metadata(station_id: str) -> StationMetadata:
    sid = str(int(station_id)).zfill(3)
    meta = fetch_station_metadata_index().get(sid)
    if meta is None:
        raise KeyError(f'No metadata found for UHSLC station {sid}')
    return meta


def clean_hourly_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['time'] = pd.to_datetime(out['time'], utc=True).dt.tz_localize(None)
    out = out.sort_values('time').drop_duplicates('time')
    out['sea_level'] = pd.to_numeric(out['sea_level'], errors='coerce').astype(float)
    out.loc[out['sea_level'] == MISSING_VALUE, 'sea_level'] = np.nan
    return out.reset_index(drop=True)


def expected_hourly_count(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return int(((end - start) / pd.Timedelta(hours=1)) + 1)


def epoch_completion(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> Tuple[int, int, float]:
    mask = (df['time'] >= start) & (df['time'] <= end)
    sub = df.loc[mask, ['time', 'sea_level']].copy()
    expected = expected_hourly_count(start, end)
    valid = int(sub['sea_level'].notna().sum())
    fraction = valid / expected if expected else 0.0
    return expected, valid, fraction


def annual_completion_fractions(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> List[float]:
    fractions = []
    for year in range(start.year, end.year + 1):
        year_start = max(pd.Timestamp(year=year, month=1, day=1), start)
        year_end = min(pd.Timestamp(year=year, month=12, day=31, hour=23), end)
        expected, _valid_n, frac = epoch_completion(df, year_start, year_end)
        if expected:
            fractions.append(frac)
    return fractions


def has_minimum_annual_completion(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, min_fraction: float) -> bool:
    fractions = annual_completion_fractions(df, start, end)
    return bool(fractions) and all(frac >= min_fraction for frac in fractions)


def select_prediction_epoch(df: pd.DataFrame, min_annual_fraction: float = 0.75) -> Epoch | None:
    valid = df.dropna(subset=['sea_level'])
    if valid.empty:
        return None

    first_year = int(valid['time'].min().year)
    last_year = int(valid['time'].max().year)
    best = None
    for start_year in range(first_year, last_year - 17):
        start = pd.Timestamp(year=start_year, month=1, day=1)
        end = pd.Timestamp(year=start_year + 18, month=12, day=31, hour=23)
        if not has_minimum_annual_completion(df, start, end, min_annual_fraction):
            continue
        expected, valid_n, frac = epoch_completion(df, start, end)
        candidate = (frac, valid_n, end, start, expected)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    frac, valid_n, end, start, expected = best
    return Epoch(
        name=f'PRED_{start.year}_{end.year}',
        start=start,
        end=end,
        source='prediction',
        role='harmonic_prediction',
        completion_fraction=frac,
        n_expected=expected,
        n_valid=valid_n,
    )


def select_epochs(df: pd.DataFrame, min_fraction: float = 0.75, min_months_recent: int = 6) -> List[Epoch]:
    df = clean_hourly_dataframe(df)
    epochs: List[Epoch] = []
    for name, start, end in PRIMARY_EPOCHS:
        expected, valid_n, frac = epoch_completion(df, start, end)
        if frac >= min_fraction:
            epochs.append(Epoch(name=name, start=start, end=end, source='primary', completion_fraction=frac, n_expected=expected, n_valid=valid_n))

    if epochs:
        if not any(has_minimum_annual_completion(df, e.start, e.end, min_fraction) for e in epochs):
            pred_epoch = select_prediction_epoch(df, min_annual_fraction=min_fraction)
            if pred_epoch is not None and pred_epoch.name not in {e.name for e in epochs}:
                epochs.append(pred_epoch)
        return epochs[:4]

    valid = df.dropna(subset=['sea_level'])
    if valid.empty:
        return []

    data_start = valid['time'].min().floor('h')
    data_end = valid['time'].max().floor('h')
    min_span = pd.Timedelta(days=30 * min_months_recent)
    if (data_end - data_start) < min_span:
        return []

    best = None
    for months in range(228, min_months_recent - 1, -1):
        start = data_end - pd.DateOffset(months=months)
        if start < data_start:
            start = data_start
        if (data_end - start) < min_span:
            continue
        expected, valid_n, frac = epoch_completion(df, start, data_end)
        if frac > min_fraction:
            best = (start, data_end, expected, valid_n, frac)
            break

    if best is not None:
        start, end, expected, valid_n, frac = best
        epochs.append(Epoch(name=f'RECENT_{start.date()}_{end.date()}', start=start, end=end, source='recent', completion_fraction=frac, n_expected=expected, n_valid=valid_n))
    return epochs[:3]


def _tidal_day_windows(times: pd.Series, values: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    t0 = times.iloc[0]
    t1 = times.iloc[-1]
    tidal_day = pd.Timedelta(hours=24, minutes=50)
    starts = []
    cur = t0
    while cur + tidal_day <= t1:
        starts.append(cur)
        cur += tidal_day
    max_list, min_list, mean_list = [], [], []
    for s in starts:
        e = s + tidal_day
        mask = (times >= s) & (times < e)
        window = values[mask.to_numpy()]
        if np.isfinite(window).sum() >= 20:
            max_list.append(np.nanmax(window))
            min_list.append(np.nanmin(window))
            mean_list.append(np.nanmean(window))
    return np.array(max_list, dtype=float), np.array(min_list, dtype=float), np.array(mean_list, dtype=float)


def _classify_tide_type(times: pd.Series, values: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> str:
    tidal_day = pd.Timedelta(hours=24, minutes=50)
    valid_windows = []
    cur = times.iloc[0]
    last = times.iloc[-1]
    while cur + tidal_day <= last:
        end = cur + tidal_day
        mask = (times >= cur) & (times < end)
        window_values = values[mask.to_numpy()]
        if np.isfinite(window_values).sum() >= 20:
            valid_windows.append((cur, end, np.nanmax(window_values) - np.nanmin(window_values)))
        cur += tidal_day

    if not valid_windows:
        return 'Unknown'

    high_times = times.iloc[highs].reset_index(drop=True)
    low_times = times.iloc[lows].reset_index(drop=True)
    high_values = values[highs]
    low_values = values[lows]
    high_counts = []
    low_counts = []
    high_diffs = []
    low_diffs = []
    ranges = []

    for start, end, window_range in valid_windows:
        hmask = (high_times >= start) & (high_times < end)
        lmask = (low_times >= start) & (low_times < end)
        hvals = high_values[hmask.to_numpy()]
        lvals = low_values[lmask.to_numpy()]
        high_counts.append(len(hvals))
        low_counts.append(len(lvals))
        if np.isfinite(window_range) and window_range > 0:
            ranges.append(window_range)
        if len(hvals) >= 2:
            high_diffs.append(abs(float(hvals[0]) - float(hvals[1])))
        if len(lvals) >= 2:
            low_diffs.append(abs(float(lvals[0]) - float(lvals[1])))

    typical_highs = int(np.rint(np.nanmedian(high_counts)))
    typical_lows = int(np.rint(np.nanmedian(low_counts)))
    if typical_highs <= 1 and typical_lows <= 1:
        return 'Diurnal'
    if typical_highs < 2 or typical_lows < 2 or not ranges:
        return 'Unknown'

    range_scale = float(np.nanmedian(ranges))
    high_inequality = float(np.nanmedian(high_diffs)) if high_diffs else 0.0
    low_inequality = float(np.nanmedian(low_diffs)) if low_diffs else 0.0
    equality_limit = TIDE_TYPE_EQUALITY_FRACTION * range_scale
    if high_inequality <= equality_limit and low_inequality <= equality_limit:
        return 'Semidiurnal'
    return 'Mixed Semidiurnal'


def compute_tide_type(df_epoch: pd.DataFrame) -> str:
    df_epoch = clean_hourly_dataframe(df_epoch)
    df_epoch = df_epoch.dropna(subset=['sea_level'])
    if len(df_epoch) < 24:
        return 'Unknown'
    y = df_epoch['sea_level'].to_numpy(dtype=float)
    t = df_epoch['time']
    highs, _ = find_peaks(y, distance=6)
    lows, _ = find_peaks(-y, distance=6)
    return _classify_tide_type(t, y, highs, lows)


def compute_datums(df_epoch: pd.DataFrame, epoch_prediction: pd.DataFrame | None = None) -> DatumResult:
    df_epoch = clean_hourly_dataframe(df_epoch)
    df_epoch = df_epoch.dropna(subset=['sea_level'])
    if len(df_epoch) < 24:
        raise ValueError('Not enough valid hourly data to compute datums.')

    y = df_epoch['sea_level'].to_numpy(dtype=float)
    t = df_epoch['time']
    highs, _ = find_peaks(y, distance=6)
    lows, _ = find_peaks(-y, distance=6)
    hw = y[highs] if len(highs) else np.array([np.nan])
    lw = y[lows] if len(lows) else np.array([np.nan])
    MHW = float(np.nanmean(hw))
    MLW = float(np.nanmean(lw))

    tide_type = _classify_tide_type(t, y, highs, lows)

    max_list, min_list, _ = _tidal_day_windows(t, y)
    MHHW = float(np.nanmean(max_list)) if len(max_list) else np.nan
    MLLW = float(np.nanmean(min_list)) if len(min_list) else np.nan
    DTL = float((MHHW + MLLW) / 2.0)
    MTL = float((MHW + MLW) / 2.0)
    MSL = float(np.nanmean(y))
    GT = float(MHHW - MLLW)
    MN = float(MHW - MLW)
    DHQ = float(MHHW - MHW)
    DLQ = float(MLW - MLLW)
    pred_y = y
    if epoch_prediction is not None:
        pred_df = epoch_prediction.copy()
        if 'prediction_mm' not in pred_df.columns:
            raise ValueError("epoch_prediction must include a 'prediction_mm' column.")
        pred_y = pd.to_numeric(pred_df['prediction_mm'], errors='coerce').to_numpy(dtype=float)
        pred_y = pred_y[np.isfinite(pred_y)]
        if len(pred_y) == 0:
            raise ValueError('epoch_prediction must contain at least one finite predicted value.')

    HAT = float(np.nanmax(pred_y))
    LAT = float(np.nanmin(pred_y))

    p90_low = float(np.nanpercentile(y, 10))
    p95_low = float(np.nanpercentile(y, 5))
    p99_low = float(np.nanpercentile(y, 1))
    p90_high = float(np.nanpercentile(y, 90))
    p95_high = float(np.nanpercentile(y, 95))
    p99_high = float(np.nanpercentile(y, 99))

    return DatumResult(tide_type=tide_type, MHHW=MHHW, MHW=MHW, DTL=DTL, MTL=MTL, MSL=MSL, MLW=MLW, MLLW=MLLW, GT=GT, MN=MN, DHQ=DHQ, DLQ=DLQ, HAT=HAT, LAT=LAT, p90_low=p90_low, p95_low=p95_low, p99_low=p99_low, p90_high=p90_high, p95_high=p95_high, p99_high=p99_high)


def fit_harmonics(df_epoch: pd.DataFrame, latitude: float) -> HarmonicResult:
    df_epoch = clean_hourly_dataframe(df_epoch)
    df_epoch = df_epoch.dropna(subset=['sea_level'])
    if len(df_epoch) < 24 * 30:
        raise ValueError('Need at least ~30 days of valid hourly data for harmonic analysis.')

    t = df_epoch['time']
    y_mm = df_epoch['sea_level'].to_numpy(dtype=float)
    # Legacy Matlab used UTide on epoch datetimes with nodal corrections enabled
    # and with a linear trend retained in the fit. The Python package can accept
    # datetime arrays directly; passing matplotlib datenums here causes UTide to
    # misinterpret the sampling interval and return an empty constituent set.
    coef = solve(
        t.to_numpy(),
        y_mm,
        lat=float(latitude),
        trend=True,
        method='ols',
        nodal=True,
        # Legacy Matlab uses UTide with "opt = 'nostats'". Disabling confidence
        # interval estimation materially reduces memory/CPU pressure on long
        # epochs while still returning the fitted constituent set.
        conf_int='none',
        verbose=False,
    )

    names = [str(c) for c in np.atleast_1d(coef.name)] if hasattr(coef, 'name') else []
    amps = [float(v) for v in np.atleast_1d(coef.A)] if hasattr(coef, 'A') else []
    phases = [float(v) for v in np.atleast_1d(coef.g)] if hasattr(coef, 'g') else []
    mean_mm = float(getattr(coef, 'mean', np.nanmean(y_mm)))
    slope = float(getattr(coef, 'slope', 0.0))
    return HarmonicResult(constituent=names, amplitude_mm=amps, phase_deg=phases, mean_mm=mean_mm, slope_mm_per_day=slope, coef=coef)


def strip_harmonic_result(harmonics: HarmonicResult) -> HarmonicResult:
    return HarmonicResult(
        constituent=list(harmonics.constituent),
        amplitude_mm=list(harmonics.amplitude_mm),
        phase_deg=list(harmonics.phase_deg),
        mean_mm=float(harmonics.mean_mm),
        slope_mm_per_day=float(harmonics.slope_mm_per_day),
        coef=None,
    )


def save_harmonic_result(harmonics: HarmonicResult, path: str, metadata: dict | None = None) -> dict:
    outpath = Path(path)
    outpath.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'metadata': metadata or {},
        'harmonics': {
            'constituent': list(harmonics.constituent),
            'amplitude_mm': list(harmonics.amplitude_mm),
            'phase_deg': list(harmonics.phase_deg),
            'mean_mm': float(harmonics.mean_mm),
            'slope_mm_per_day': float(harmonics.slope_mm_per_day),
        },
        'coef': harmonics.coef,
    }
    with outpath.open('wb') as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    json_path = outpath.with_suffix('.json')
    json_payload = {
        'metadata': metadata or {},
        'harmonics': payload['harmonics'],
    }
    json_path.write_text(json.dumps(json_payload, indent=2, default=str))
    return {'pickle': str(outpath), 'json': str(json_path)}


def load_harmonic_result(path: str) -> HarmonicResult:
    with Path(path).open('rb') as f:
        payload = pickle.load(f)
    harmonics = payload.get('harmonics', {})
    return HarmonicResult(
        constituent=[str(v) for v in harmonics.get('constituent', [])],
        amplitude_mm=[float(v) for v in harmonics.get('amplitude_mm', [])],
        phase_deg=[float(v) for v in harmonics.get('phase_deg', [])],
        mean_mm=float(harmonics.get('mean_mm', np.nan)),
        slope_mm_per_day=float(harmonics.get('slope_mm_per_day', 0.0)),
        coef=payload.get('coef'),
    )


def predict_from_harmonics(harmonics: HarmonicResult, start: pd.Timestamp, end: pd.Timestamp, freq: str = '1h') -> pd.DataFrame:
    if end < start:
        raise ValueError('Prediction end precedes start.')
    time = pd.date_range(start=start, end=end, freq=freq)
    if len(time) == 0:
        return pd.DataFrame({'time': [], 'prediction_mm': []})
    if harmonics.coef is None:
        raise ValueError('UTide coefficients not available for reconstruction.')
    coef = copy.deepcopy(harmonics.coef)
    if hasattr(coef, 'slope'):
        coef.slope = 0.0
    # With conf_int='none' above, reconstruct cannot rely on SNR-based
    # filtering. Use the fitted constituent list directly, matching the legacy
    # "use solved coefficients, zero trend, reconstruct" workflow.
    recon = reconstruct(
        time.to_numpy(),
        coef,
        constit=np.asarray(harmonics.constituent, dtype=object),
        min_SNR=0,
        verbose=False,
    )
    pred = np.asarray(recon.h, dtype=float)
    return pd.DataFrame({'time': time, 'prediction_mm': pred})


def extract_daily_high_low(minute_pred_df: pd.DataFrame) -> pd.DataFrame:
    df = minute_pred_df.copy()
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time')
    y = df['prediction_mm'].to_numpy(dtype=float)
    peaks, _ = find_peaks(y, distance=60*6)
    troughs, _ = find_peaks(-y, distance=60*6)
    events = pd.concat([
        pd.DataFrame({'time': df.iloc[peaks]['time'].to_numpy(), 'height_mm': y[peaks], 'type': 'H'}),
        pd.DataFrame({'time': df.iloc[troughs]['time'].to_numpy(), 'height_mm': y[troughs], 'type': 'L'}),
    ], ignore_index=True).sort_values('time')
    if events.empty:
        return events
    events['date'] = events['time'].dt.date
    rows = []
    for _, grp in events.groupby('date'):
        highs = grp[grp['type'] == 'H'].nlargest(2, 'height_mm')
        lows = grp[grp['type'] == 'L'].nsmallest(2, 'height_mm')
        rows.append(pd.concat([highs, lows]).sort_values('time'))
    out = pd.concat(rows, ignore_index=True).sort_values('time') if rows else pd.DataFrame(columns=['time','height_mm','type','date'])
    return out[['time', 'height_mm', 'type']]


def extract_daily_high_low_chunked(
    harmonics: HarmonicResult,
    start: pd.Timestamp,
    end: pd.Timestamp,
    chunk_days: int = 31,
    pad_hours: int = 18,
) -> pd.DataFrame:
    if end < start:
        return pd.DataFrame(columns=['time', 'height_mm', 'type'])

    rows = []
    chunk_start = pd.Timestamp(start)
    final_end = pd.Timestamp(end)
    pad = pd.Timedelta(hours=pad_hours)

    while chunk_start <= final_end:
        chunk_end = min(chunk_start + pd.Timedelta(days=chunk_days) - pd.Timedelta(minutes=1), final_end)
        padded_start = max(start, chunk_start - pad)
        padded_end = min(final_end, chunk_end + pad)
        minute_df = predict_from_harmonics(harmonics, padded_start, padded_end, freq='1min')
        hl = extract_daily_high_low(minute_df)
        if not hl.empty:
            chunk_dates = pd.date_range(chunk_start.floor('D'), chunk_end.floor('D'), freq='D')
            keep_dates = set(chunk_dates.date)
            keep = hl['time'].dt.date.isin(keep_dates)
            rows.append(hl.loc[keep].copy())
        chunk_start = chunk_end + pd.Timedelta(minutes=1)

    if not rows:
        return pd.DataFrame(columns=['time', 'height_mm', 'type'])

    out = pd.concat(rows, ignore_index=True).sort_values('time')
    out = out.drop_duplicates(subset=['time', 'type']).reset_index(drop=True)
    return out[['time', 'height_mm', 'type']]


def predict_fd_high_low(
    harmonics: HarmonicResult,
    end: pd.Timestamp | None = None,
    chunk_days: int = 31,
) -> pd.DataFrame:
    minute_end = min(pd.Timestamp(end), MAX_MINUTE_PREDICTION_END) if end is not None else MAX_MINUTE_PREDICTION_END
    minute_start = MAX_MINUTE_PREDICTION_START
    if minute_end <= minute_start:
        return pd.DataFrame(columns=['time', 'height_mm', 'type'])
    return extract_daily_high_low_chunked(
        harmonics,
        minute_start,
        minute_end,
        chunk_days=chunk_days,
    )


def _round_mm_array(values):
    arr = np.asarray(values)
    if np.issubdtype(arr.dtype, np.floating):
        return np.rint(arr).astype(np.int32)
    return arr


def _attach_switch_levels(ds: xr.Dataset, epochs: List[Epoch], switch_levels: SwitchLevel | None) -> xr.Dataset:
    if switch_levels is None:
        lev = np.full(len(epochs), np.nan, dtype=float)
        levb = np.full(len(epochs), np.nan, dtype=float)
        switch_date = np.full(len(epochs), '', dtype=object)
    else:
        lev_value = np.nan if switch_levels.LEV is None else float(switch_levels.LEV)
        levb_value = np.nan if switch_levels.LEVB is None else float(switch_levels.LEVB)
        date_value = '' if not switch_levels.Date else str(switch_levels.Date)
        lev = np.full(len(epochs), lev_value, dtype=float)
        levb = np.full(len(epochs), levb_value, dtype=float)
        switch_date = np.full(len(epochs), date_value, dtype=object)

    ds['LEV'] = xr.DataArray(lev, dims=['epoch'])
    ds['LEVB'] = xr.DataArray(levb, dims=['epoch'])
    ds['switch_date'] = xr.DataArray(switch_date, dims=['epoch'])
    return ds


def _attach_skill(ds: xr.Dataset) -> xr.Dataset:
    skill = get_netcdf_skill_reference()
    ds.attrs['skill_format'] = skill.format
    ds.attrs['skill_name'] = skill.name
    ds.attrs['skill_description'] = skill.description
    ds.attrs['skill_local_path'] = skill.local_path
    ds.attrs['skill_remote_url'] = skill.remote_url
    ds.attrs['skill_location'] = f'local: {skill.local_path}; remote: {skill.remote_url}'
    return ds


def build_datums_only_dataset(station_id: str, station_name: str, station_kind: str, epochs: List[Epoch], datum_by_epoch: Dict[str, DatumResult], switch_levels: SwitchLevel | None = None) -> xr.Dataset:
    ds = xr.Dataset(coords={'epoch': [e.name for e in epochs]})
    ds.attrs['station_id'] = station_id
    ds.attrs['station_name'] = station_name
    ds.attrs['station_kind'] = station_kind
    ds.attrs['reference_frame'] = 'Station Zero'
    ds.attrs['time_zone'] = 'GMT'
    ds.attrs['units'] = 'mm integer'
    ds.attrs['content'] = 'datums_only'

    for field in DatumResult.__dataclass_fields__.keys():
        if field == 'tide_type':
            ds[field] = xr.DataArray(np.array([datum_by_epoch[e.name].tide_type for e in epochs], dtype=object), dims=['epoch'])
        else:
            vals = [round(getattr(datum_by_epoch[e.name], field)) for e in epochs]
            ds[field] = xr.DataArray(np.array(vals, dtype=np.int32), dims=['epoch'])

    ds['epoch_start'] = xr.DataArray(np.array([np.datetime64(e.start, 'ns') for e in epochs]), dims=['epoch'])
    ds['epoch_end'] = xr.DataArray(np.array([np.datetime64(e.end, 'ns') for e in epochs]), dims=['epoch'])
    ds['epoch_completion_fraction'] = xr.DataArray(np.array([e.completion_fraction for e in epochs], dtype=float), dims=['epoch'])
    ds['epoch_source'] = xr.DataArray(np.array([e.source for e in epochs], dtype=object), dims=['epoch'])
    ds['epoch_role'] = xr.DataArray(np.array([e.role for e in epochs], dtype=object), dims=['epoch'])
    ds['epoch_n_expected'] = xr.DataArray(np.array([e.n_expected for e in epochs], dtype=np.int32), dims=['epoch'])
    ds['epoch_n_valid'] = xr.DataArray(np.array([e.n_valid for e in epochs], dtype=np.int32), dims=['epoch'])
    ds = _attach_switch_levels(ds, epochs, switch_levels)
    return _attach_skill(ds)


def build_netcdf_dataset(station_id: str, station_name: str, station_kind: str, epochs: List[Epoch], datum_by_epoch: Dict[str, DatumResult], harmonics_by_epoch: Dict[str, HarmonicResult], hourly_predictions: Dict[str, pd.DataFrame], switch_levels: SwitchLevel | None = None) -> xr.Dataset:
    epoch_names = [e.name for e in epochs]
    max_const = max((len(harmonics_by_epoch[e].constituent for e in epoch_names)), default=0) if False else max((len(harmonics_by_epoch[e].constituent) for e in epoch_names), default=0)
    const_arr = np.full((len(epoch_names), max_const), '', dtype=object)
    amp_arr = np.full((len(epoch_names), max_const), np.nan, dtype=float)
    phase_arr = np.full((len(epoch_names), max_const), np.nan, dtype=float)
    for i, e in enumerate(epoch_names):
        hr = harmonics_by_epoch[e]
        n = len(hr.constituent)
        const_arr[i, :n] = hr.constituent
        amp_arr[i, :n] = np.rint(hr.amplitude_mm)
        phase_arr[i, :n] = hr.phase_deg

    ds = xr.Dataset(coords={'epoch': epoch_names, 'constituent_index': np.arange(max_const, dtype=int)})
    ds.attrs['station_id'] = station_id
    ds.attrs['station_name'] = station_name
    ds.attrs['station_kind'] = station_kind
    ds.attrs['reference_frame'] = 'Station Zero'
    ds.attrs['time_zone'] = 'GMT'
    ds.attrs['units'] = 'mm integer'

    for field in DatumResult.__dataclass_fields__.keys():
        if field == 'tide_type':
            ds[field] = xr.DataArray(np.array([datum_by_epoch[e.name].tide_type for e in epochs], dtype=object), dims=['epoch'])
        else:
            vals = [round(getattr(datum_by_epoch[e.name], field)) for e in epochs]
            ds[field] = xr.DataArray(np.array(vals, dtype=np.int32), dims=['epoch'])
    ds['epoch_start'] = xr.DataArray(np.array([np.datetime64(e.start, 'ns') for e in epochs]), dims=['epoch'])
    ds['epoch_end'] = xr.DataArray(np.array([np.datetime64(e.end, 'ns') for e in epochs]), dims=['epoch'])
    ds['epoch_completion_fraction'] = xr.DataArray(np.array([e.completion_fraction for e in epochs], dtype=float), dims=['epoch'])
    ds['epoch_source'] = xr.DataArray(np.array([e.source for e in epochs], dtype=object), dims=['epoch'])
    ds['epoch_role'] = xr.DataArray(np.array([e.role for e in epochs], dtype=object), dims=['epoch'])
    ds['epoch_n_expected'] = xr.DataArray(np.array([e.n_expected for e in epochs], dtype=np.int32), dims=['epoch'])
    ds['epoch_n_valid'] = xr.DataArray(np.array([e.n_valid for e in epochs], dtype=np.int32), dims=['epoch'])
    ds['harmonic_constituent'] = xr.DataArray(const_arr, dims=['epoch', 'constituent_index'])
    ds['harmonic_amplitude_mm'] = xr.DataArray(_round_mm_array(amp_arr), dims=['epoch', 'constituent_index'])
    ds['harmonic_phase_deg'] = xr.DataArray(np.array(np.rint(phase_arr), dtype=np.int32), dims=['epoch', 'constituent_index'])

    for e in epoch_names:
        pred = hourly_predictions.get(e)
        if pred is not None and not pred.empty:
            ds[f'hourly_prediction_{e}'] = xr.DataArray(_round_mm_array(pred['prediction_mm'].to_numpy(dtype=float)), dims=[f'time_{e}'], coords={f'time_{e}': pred['time'].to_numpy(dtype='datetime64[ns]')})

    ds = _attach_switch_levels(ds, epochs, switch_levels)
    return _attach_skill(ds)


def save_netcdf(ds: xr.Dataset, path: str) -> None:
    encoding = {}
    for name, var in ds.data_vars.items():
        if np.issubdtype(var.dtype, np.integer):
            encoding[name] = {'zlib': True, 'complevel': 4}
        elif np.issubdtype(var.dtype, np.floating):
            encoding[name] = {'zlib': True, 'complevel': 4, '_FillValue': np.nan}
    ds.to_netcdf(path, encoding=encoding)


def fetch_fd_hourly(station_id: str, start: str = '1800-01-01', end: str = '2100-12-31') -> pd.DataFrame:
    sid = int(station_id)
    url = f"{ERDDAP_BASE}/global_hourly_fast.csvp?sea_level,time,uhslc_id,record_id,station_name,station_country&uhslc_id={sid}&time>={start}T00:00:00Z&time<={end}T23:59:59Z"
    df = load_erddap_csv(url)
    df['time'] = snap_to_hour(df['time'])
    df['sea_level'] = pd.to_numeric(df['sea_level'], errors='coerce')
    return df[['time','sea_level','uhslc_id','record_id','station_name','station_country']]


def fetch_rq_hourly(station_id: str, version: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    sid = int(station_id)
    version = str(version).upper()
    url = f"{ERDDAP_BASE}/global_hourly_rqds.csvp?sea_level,time,uhslc_id,record_id,station_name,station_country,version&uhslc_id={sid}&version=%22{version}%22"
    if start is not None and end is not None:
        url += f"&time>={start}T00:00:00Z&time<={end}T23:59:59Z"
    df = load_erddap_csv(url)
    df['time'] = snap_to_hour(df['time'])
    df['sea_level'] = pd.to_numeric(df['sea_level'], errors='coerce')
    return df[['time','sea_level','uhslc_id','record_id','station_name','station_country','version']]


def list_rq_versions(station_ids: list[str]) -> dict:
    out = {}
    metadata = fetch_station_metadata_index()
    for sid in station_ids:
        key = str(int(sid)).zfill(3)
        meta = metadata.get(key)
        versions = []
        if meta is not None:
            versions = sorted(v.upper() for v in meta.rq_versions.keys())
        out[key] = versions
    return out


def get_rq_metadata_span(station_id: str, version: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    sid = str(int(station_id)).zfill(3)
    version = str(version).upper()
    try:
        meta = get_station_metadata(sid)
        version_meta = meta.rq_versions.get(version.lower()) or meta.rq_versions.get(version.upper())
        if version_meta:
            start = pd.to_datetime(version_meta.get('begin'), utc=True).tz_localize(None)
            end = pd.to_datetime(version_meta.get('end'), utc=True).tz_localize(None)
            return start, end
    except KeyError:
        pass

    url = f"{RQ_META_INDEX}{station_id}{version}meta.yaml"
    r = requests.get(url, timeout=60)
    if not r.ok:
        raise RuntimeError(f'Failed to fetch RQ metadata yaml: {url}')
    meta = yaml.safe_load(r.text)
    td = meta.get('Time_Details', {}) if isinstance(meta, dict) else {}
    start = pd.to_datetime(td.get('Date_Start'), utc=True).tz_localize(None)
    end = pd.to_datetime(td.get('Date_End'), utc=True).tz_localize(None)
    return start, end
