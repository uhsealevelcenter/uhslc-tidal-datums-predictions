from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys
import gc

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import (
    build_prediction_save_plan,
    build_netcdf_dataset,
    clean_hourly_dataframe,
    compute_datums,
    fetch_fd_hourly,
    fetch_rq_hourly,
    fit_harmonics,
    load_harmonic_result,
    predict_minute_high_low,
    predict_from_harmonics,
    saved_minute_highlow_epoch_items,
    saved_prediction_epoch_items,
    save_harmonic_result,
    save_netcdf,
    select_epochs,
    strip_harmonic_result,
    fetch_station_metadata_index,
    get_station_metadata,
    get_station_switch_levels,
    list_rq_versions,
)


DEFAULT_STATION_ID = "007"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _resolve_station_ids(value: str) -> list[str]:
    value = str(value).strip()
    if value.lower() == "all":
        return sorted(fetch_station_metadata_index().keys())
    return [str(int(part.strip())).zfill(3) for part in value.split(",") if part.strip()]


def _compute_residual_metrics(merged: pd.DataFrame) -> dict:
    residual = (
        pd.to_numeric(merged["sea_level"], errors="coerce")
        - pd.to_numeric(merged["prediction_mm"], errors="coerce")
    )
    residual = residual[np.isfinite(residual)]

    if residual.empty:
        return {
            "residual_rows": 0,
            "epoch_rmse_mm": None,
            "epoch_residual_mean_mm": None,
            "epoch_residual_std_mm": None,
            "epoch_residual_abs_p95_mm": None,
        }

    return {
        "residual_rows": int(len(residual)),
        "epoch_rmse_mm": float(np.sqrt(np.mean(np.square(residual)))),
        "epoch_residual_mean_mm": float(np.mean(residual)),
        "epoch_residual_std_mm": float(np.std(residual)),
        "epoch_residual_abs_p95_mm": float(np.nanpercentile(np.abs(residual), 95)),
    }


def _plot_hourly_comparison(plot_path: Path, merged: pd.DataFrame, title: str) -> dict:
    plot_df = merged.head(24 * 31).copy()

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(plot_df["time"], plot_df["sea_level"], label="Observed", linewidth=1.0)
    ax.plot(plot_df["time"], plot_df["prediction_mm"], label="Predicted", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("Sea Level (mm, station zero)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    residual = plot_df["sea_level"] - plot_df["prediction_mm"]

    return {
        "comparison_plot": str(plot_path),
        "plot_window_rows": int(len(plot_df)),
        "plot_window_rmse_mm": float(np.sqrt(np.mean(np.square(residual)))) if len(plot_df) else None,
    }


def _plot_datums(plot_path: Path, series: pd.DataFrame, datum, switch_levels, title: str) -> str:
    plot_df = series.dropna(subset=["sea_level"]).head(24 * 31).copy()
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(plot_df["time"], plot_df["sea_level"], color="0.25", linewidth=0.9, label="Observed hourly sea level")
    datum_lines = {
        "MHHW": datum.MHHW,
        "MHW": datum.MHW,
        "MSL": datum.MSL,
        "MLW": datum.MLW,
        "MLLW": datum.MLLW,
    }
    colors = {
        "MHHW": "tab:blue",
        "MHW": "tab:cyan",
        "MSL": "tab:green",
        "MLW": "tab:orange",
        "MLLW": "tab:red",
    }
    if switch_levels is not None:
        if switch_levels.LEV is not None:
            datum_lines["LEV"] = float(switch_levels.LEV)
            colors["LEV"] = "tab:purple"
        if switch_levels.LEVB is not None:
            datum_lines["LEVB"] = float(switch_levels.LEVB)
            colors["LEVB"] = "tab:brown"
    for name, value in datum_lines.items():
        ax.axhline(value, color=colors[name], linestyle="--", linewidth=1.1, label=f"{name} = {value:.1f} mm")
    ax.set_title(title)
    ax.set_ylabel("Sea Level (mm, station zero)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return str(plot_path)


def _plot_residuals(plot_path: Path, merged: pd.DataFrame, title: str) -> str:
    plot_df = merged.copy()
    residual = plot_df["sea_level"] - plot_df["prediction_mm"]

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(plot_df["time"], residual, color="black", linewidth=0.6)
    ax.axhline(0.0, color="tab:red", linestyle="--", linewidth=1.0)
    ax.set_title(title)
    ax.set_ylabel("Observed - Predicted (mm)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    return str(plot_path)


def _plot_fd_high_low(plot_path: Path, high_low: pd.DataFrame, title: str) -> str | None:
    if high_low.empty:
        return None
    plot_df = high_low.head(120).copy()
    colors = plot_df["type"].map({"H": "tab:blue", "L": "tab:orange"}).to_numpy()
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.scatter(plot_df["time"], plot_df["height_mm"], c=colors, s=18)
    ax.plot(plot_df["time"], plot_df["height_mm"], color="0.5", linewidth=0.8, alpha=0.7)
    ax.set_title(title)
    ax.set_ylabel("Predicted Tide (mm relative to station zero)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return str(plot_path)


def _run_record(
    station_id: str,
    station_kind: str,
    outdir: Path,
    version: str | None = None,
) -> dict:
    requested_record = station_id if station_kind == "FD" else f"{station_id}{str(version).lower()}"
    log(f"Starting {station_kind} record {requested_record}")
    station_meta = get_station_metadata(station_id)
    switch_levels = get_station_switch_levels(station_id)
    latitude = station_meta.latitude
    if station_kind == "FD":
        log(f"Fetching FD hourly data for station {station_id}")
        raw = fetch_fd_hourly(station_id)
        record_id = station_id
    else:
        assert version is not None
        log(f"Fetching RQ hourly data for station {station_id} version {version}")
        raw = fetch_rq_hourly(station_id, version)
        record_id = f"{station_id}{version.lower()}"

    station_name = str(raw["station_name"].dropna().iloc[0])
    df = clean_hourly_dataframe(raw[["time", "sea_level"]])
    valid_rows = int(df["sea_level"].notna().sum())
    log(f"{record_id}: loaded {len(df)} hourly rows ({valid_rows} valid)")
    epochs = select_epochs(df)
    if not epochs:
        raise RuntimeError(f"No qualifying epochs found for {record_id}")
    log(f"{record_id}: selected epochs: {', '.join(ep.name for ep in epochs)}")

    datum_by_epoch = {}
    harmonics_by_epoch = {}
    hourly_predictions = {}
    harmonic_artifacts = {}
    minute_highlow_by_epoch = {}
    hourly_prediction_summaries = []
    minute_highlow_prediction_summaries = []
    epoch_summaries = []
    epoch_summary_by_name = {}
    prediction_plan = build_prediction_save_plan(df, epochs, station_kind, station_id=station_id, version=version)
    log(
        f"{record_id}: prediction basis {prediction_plan.basis_epoch}; "
        f"hourly {prediction_plan.hourly_start} to {prediction_plan.hourly_end}; "
        f"minute high/low saved={prediction_plan.save_minute_high_low}"
    )
    plot_dir = outdir / "plots" / record_id
    plot_dir.mkdir(parents=True, exist_ok=True)

    for ep in epochs:
        log(f"{record_id} {ep.name}: fitting harmonics and calculating datums")
        sub = df[(df["time"] >= ep.start) & (df["time"] <= ep.end)].copy()
        fitted_harmonics = fit_harmonics(sub, latitude=latitude)
        harmonic_path = outdir / "harmonics" / record_id / f"{ep.name}_harmonics.pkl"
        harmonic_artifacts[ep.name] = save_harmonic_result(
            fitted_harmonics,
            str(harmonic_path),
            metadata={
                "station_id": record_id,
                "station_name": station_name,
                "station_kind": station_kind,
                "epoch_name": ep.name,
                "epoch_start": str(ep.start),
                "epoch_end": str(ep.end),
                "latitude": float(latitude),
            },
        )
        harmonics_summary = strip_harmonic_result(fitted_harmonics)
        del fitted_harmonics
        gc.collect()

        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])
        epoch_hourly_pred = predict_from_harmonics(harmonics, ep.start, ep.end, freq="1h")
        datum = compute_datums(sub, epoch_prediction=epoch_hourly_pred)

        datum_by_epoch[ep.name] = datum
        harmonics_by_epoch[ep.name] = harmonics_summary
        minute_highlow = pd.DataFrame(columns=["time", "height_mm", "type"])

        observed = sub.dropna(subset=["sea_level"])[["time", "sea_level"]].copy()
        datum_plot = _plot_datums(
            plot_dir / f"{ep.name}_datums.png",
            sub,
            datum,
            switch_levels,
            f"{record_id} {ep.name}: tidal datums",
        )
        within_epoch_pred = epoch_hourly_pred.copy()
        merged = observed.merge(within_epoch_pred, on="time", how="inner")
        residual_meta = _compute_residual_metrics(merged)
        compare_meta = _plot_hourly_comparison(
            plot_dir / f"{ep.name}_hourly_observed_vs_predicted.png",
            merged,
            f"{record_id} {ep.name}: observed vs predicted",
        )
        residual_plot = _plot_residuals(
            plot_dir / f"{ep.name}_hourly_residuals.png",
            merged,
            f"{record_id} {ep.name}: hourly residuals",
        )
        epoch_rmse = residual_meta["epoch_rmse_mm"]
        epoch_rmse_text = "nan" if epoch_rmse is None else f"{epoch_rmse:.2f}"
        log(
            f"{record_id} {ep.name}: saved harmonic artifact and plots; "
            f"constituents={len(harmonics.constituent)}, "
            f"epoch_rmse={epoch_rmse_text} mm"
        )

        minute_plot = None
        minute_rows = int(len(minute_highlow))
        if station_kind == "FD" and not minute_highlow.empty:
            minute_plot = _plot_fd_high_low(
                plot_dir / f"{ep.name}_fd_high_low.png",
                minute_highlow,
                f"{record_id} {ep.name}: FD high/low minute prediction",
            )

        epoch_summary = {
            "epoch": asdict(ep),
            "datum": asdict(datum),
            "harmonic_constituent_count": int(len(harmonics.constituent)),
            "top_constituents": harmonics.constituent[:12],
            "harmonic_artifact": harmonic_artifacts[ep.name],

            "hourly_prediction_rows": 0,
            "hourly_prediction_variable": None,
            "hourly_prediction_start": None,
            "hourly_prediction_end": None,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,

            "hourly_observed_rows": int(len(observed)),
            "hourly_overlap_rows": int(len(merged)),
            "epoch_residual_rows": residual_meta["residual_rows"],

            "plots": {
                "datums": datum_plot,
                "hourly_observed_vs_predicted": compare_meta["comparison_plot"],
                "hourly_residuals": residual_plot,
                "fd_high_low": minute_plot,
            },

            "plot_window_rows": compare_meta["plot_window_rows"],
            "plot_window_rmse_mm": compare_meta["plot_window_rmse_mm"],
            "epoch_rmse_mm": residual_meta["epoch_rmse_mm"],
            "epoch_residual_mean_mm": residual_meta["epoch_residual_mean_mm"],
            "epoch_residual_std_mm": residual_meta["epoch_residual_std_mm"],
            "epoch_residual_abs_p95_mm": residual_meta["epoch_residual_abs_p95_mm"],

            "fd_high_low_rows": minute_rows,
            "minute_highlow_prediction_rows": 0,
            "minute_highlow_time_variable": None,
            "minute_highlow_height_variable": None,
            "minute_highlow_type_variable": None,

            "switch_levels": None if switch_levels is None else asdict(switch_levels),
        }

        epoch_summary_by_name[ep.name] = epoch_summary
        epoch_summaries.append(epoch_summary)
        del harmonics, sub, epoch_hourly_pred, minute_highlow
        gc.collect()

    prediction_items = saved_prediction_epoch_items(epochs, prediction_plan)

    log(f"{record_id}: generating saved hourly predictions for {len(prediction_items)} epoch(s)")

    for prediction_key, ep in prediction_items:
        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])

        log(f"{record_id} {ep.name}: generating saved hourly prediction as {prediction_key}")

        hourly_predictions[prediction_key] = predict_from_harmonics(
            harmonics,
            prediction_plan.hourly_start,
            prediction_plan.hourly_end,
            freq="1h",
        )
        hourly_predictions[prediction_key].attrs["epoch_name"] = ep.name

        hourly_rows = int(len(hourly_predictions[prediction_key]))
        hourly_variable = f"hourly_prediction_{prediction_key}"

        hourly_prediction_summary = {
            "saved": True,
            "epoch": ep.name,
            "prediction_key": prediction_key,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,
            "variable": hourly_variable,
            "start": prediction_plan.hourly_start,
            "end": prediction_plan.hourly_end,
            "rows": hourly_rows,
        }
        hourly_prediction_summaries.append(hourly_prediction_summary)

        if ep.name in epoch_summary_by_name:
            epoch_summary_by_name[ep.name]["hourly_prediction_rows"] = hourly_rows
            epoch_summary_by_name[ep.name]["hourly_prediction_variable"] = hourly_variable
            epoch_summary_by_name[ep.name]["hourly_prediction_start"] = prediction_plan.hourly_start
            epoch_summary_by_name[ep.name]["hourly_prediction_end"] = prediction_plan.hourly_end

        del harmonics
        gc.collect()


    minute_items = saved_minute_highlow_epoch_items(epochs, prediction_plan)

    if minute_items:
        log(f"{record_id}: generating saved minute high/low predictions for {len(minute_items)} epoch(s)")

    for prediction_key, ep in minute_items:
        harmonics = load_harmonic_result(harmonic_artifacts[ep.name]["pickle"])

        log(f"{record_id} {ep.name}: generating saved minute high/low prediction as {prediction_key}")

        minute_highlow_by_epoch[prediction_key] = predict_minute_high_low(
            harmonics,
            start=prediction_plan.minute_start,
            end=prediction_plan.minute_end,
        )

        minute_rows = int(len(minute_highlow_by_epoch[prediction_key]))

        minute_summary = {
            "saved": True,
            "epoch": ep.name,
            "prediction_key": prediction_key,
            "is_prediction_basis": ep.name == prediction_plan.basis_epoch,
            "time_variable": f"minute_highlow_time_{prediction_key}",
            "height_variable": f"minute_highlow_height_mm_{prediction_key}",
            "type_variable": f"minute_highlow_type_{prediction_key}",
            "start": prediction_plan.minute_start,
            "end": prediction_plan.minute_end,
            "rows": minute_rows,
        }
        minute_highlow_prediction_summaries.append(minute_summary)

        if ep.name in epoch_summary_by_name:
            epoch_summary_by_name[ep.name]["minute_highlow_prediction_rows"] = minute_rows
            epoch_summary_by_name[ep.name]["minute_highlow_time_variable"] = minute_summary["time_variable"]
            epoch_summary_by_name[ep.name]["minute_highlow_height_variable"] = minute_summary["height_variable"]
            epoch_summary_by_name[ep.name]["minute_highlow_type_variable"] = minute_summary["type_variable"]

        del harmonics
        gc.collect()

    ds = build_netcdf_dataset(
        record_id,
        station_name,
        station_kind,
        epochs,
        datum_by_epoch,
        harmonics_by_epoch,
        hourly_predictions,
        switch_levels=switch_levels,
        prediction_plan=prediction_plan,
    )
    for ep_name, hl in minute_highlow_by_epoch.items():
        if not hl.empty:
            ds[f"minute_highlow_time_{ep_name}"] = ([f"minute_hl_{ep_name}"], hl["time"].to_numpy(dtype="datetime64[ns]"))
            ds[f"minute_highlow_height_mm_{ep_name}"] = ([f"minute_hl_{ep_name}"], np.rint(hl["height_mm"].to_numpy(dtype=float)).astype(np.int32))
            ds[f"minute_highlow_type_{ep_name}"] = ([f"minute_hl_{ep_name}"], hl["type"].astype(str).to_numpy())

    nc_path = outdir / "netcdf" / f"{record_id}.nc"
    nc_path.parent.mkdir(parents=True, exist_ok=True)
    save_netcdf(ds, str(nc_path))
    log(f"{record_id}: wrote NetCDF {nc_path}")

    return {
        "record_id": record_id,
        "station_name": station_name,
        "station_kind": station_kind,
        "netcdf": str(nc_path),
        "harmonic_artifacts": harmonic_artifacts,
        "prediction_basis_epoch": prediction_plan.basis_epoch,
        "prediction_scope": prediction_plan.prediction_scope,
        "hourly_prediction": next(
            item for item in hourly_prediction_summaries if item["is_prediction_basis"]
        ),
        "hourly_predictions": hourly_prediction_summaries,

        "minute_highlow_prediction": (
            next(
                item for item in minute_highlow_prediction_summaries
                if item["is_prediction_basis"]
            )
            if minute_highlow_prediction_summaries
            else {
                "saved": False,
                "time_variable": None,
                "height_variable": None,
                "type_variable": None,
                "start": None,
                "end": None,
                "rows": 0,
            }
        ),
        "minute_highlow_predictions": minute_highlow_prediction_summaries,
        "update_cycle_months": prediction_plan.update_cycle_months,
        "update_cycle_reason": prediction_plan.update_cycle_reason,
        "epochs": epoch_summaries,
    }


def _run_station(station_id: str) -> None:
    output_root = Path("artifacts") / "datums_predictions" / f"station{station_id}"
    output_root.mkdir(parents=True, exist_ok=True)
    log(f"Starting station {station_id} datums_predictions run")
    rq_versions = list_rq_versions([station_id])[station_id]
    log(f"Station {station_id}: RQ versions: {', '.join(rq_versions) if rq_versions else 'none'}")
    summary = {
        "station_id": station_id,
        "records": [],
    }

    summary["records"].append(_run_record(station_id, "FD", output_root))
    for version in rq_versions:
        summary["records"].append(_run_record(station_id, "RQ", output_root, version=version))

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    md_lines = [
        f"# Station {station_id} Datums Predictions",
        "",
        f"Output root: `{output_root}`",
        "",
    ]
    for record in summary["records"]:
        md_lines.append(f"## {record['record_id']}")
        md_lines.append(f"- Station name: {record['station_name']}")
        md_lines.append(f"- Station kind: {record['station_kind']}")
        md_lines.append(f"- NetCDF: `{record['netcdf']}`")
        md_lines.append(f"- Prediction basis epoch: `{record['prediction_basis_epoch']}`")
        md_lines.append(f"- Prediction scope: `{record['prediction_scope']}`")
        hourly_predictions_summary = record.get("hourly_predictions") or [record["hourly_prediction"]]

        if len(hourly_predictions_summary) == 1:
            hourly = hourly_predictions_summary[0]
            md_lines.append(
                f"- Saved hourly prediction: `{hourly['variable']}` from "
                f"`{hourly['start']}` to `{hourly['end']}` ({hourly['rows']} rows)"
            )
        else:
            md_lines.append("- Saved hourly predictions:")
            for hourly in hourly_predictions_summary:
                basis_label = " [prediction basis]" if hourly.get("is_prediction_basis") else ""
                md_lines.append(
                    f"  - `{hourly['epoch']}`{basis_label}: `{hourly['variable']}` from "
                    f"`{hourly['start']}` to `{hourly['end']}` ({hourly['rows']} rows)"
                )

        minute_predictions_summary = record.get("minute_highlow_predictions") or []

        if minute_predictions_summary:
            if len(minute_predictions_summary) == 1:
                minute = minute_predictions_summary[0]
                md_lines.append(
                    "- Saved minute high/low prediction: "
                    f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                    f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                )
            else:
                md_lines.append("- Saved minute high/low predictions:")
                for minute in minute_predictions_summary:
                    basis_label = " [prediction basis]" if minute.get("is_prediction_basis") else ""
                    md_lines.append(
                        f"  - `{minute['epoch']}`{basis_label}: "
                        f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                        f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                    )
        else:
            minute = record.get("minute_highlow_prediction")
            if minute and minute.get("saved"):
                md_lines.append(
                    "- Saved minute high/low prediction: "
                    f"`{minute['time_variable']}`, `{minute['height_variable']}`, `{minute['type_variable']}` "
                    f"from `{minute['start']}` to `{minute['end']}` ({minute['rows']} rows)"
                )
            else:
                md_lines.append("- Saved minute high/low prediction: none")
        md_lines.append(f"- Update cycle: {record['update_cycle_months']} months ({record['update_cycle_reason']})")
        md_lines.append("- Saved epochs/datums/harmonics:")
        for epoch in record["epochs"]:
            epoch_rmse = epoch["epoch_rmse_mm"]
            epoch_rmse_text = "nan" if epoch_rmse is None else f"{epoch_rmse:.2f}"

            md_lines.append(
                f"- {epoch['epoch']['name']}: constituents={epoch['harmonic_constituent_count']}, "
                f"hourly_overlap_rows={epoch['hourly_overlap_rows']}, "
                f"full_epoch_rmse_mm={epoch_rmse_text}, "
                f"plot_window_rows={epoch['plot_window_rows']}"
            )
        md_lines.append("")
    (output_root / "summary.md").write_text("\n".join(md_lines))
    log(f"Station {station_id}: wrote summaries to {output_root / 'summary.json'} and {output_root / 'summary.md'}")
    log(f"Finished station {station_id} datums_predictions run")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run datums/predictions diagnostics for one, multiple, or all stations.")
    parser.add_argument("--station-id", default=DEFAULT_STATION_ID, help="Station id, comma-separated ids, or 'all'.")
    args = parser.parse_args()

    station_ids = _resolve_station_ids(args.station_id)
    log(f"Requested datums_predictions run for {len(station_ids)} station(s): {', '.join(station_ids)}")
    for station_id in station_ids:
        _run_station(station_id)


if __name__ == "__main__":
    main()
