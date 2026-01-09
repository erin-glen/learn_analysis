#!/usr/bin/env python3
"""
final_disturbance_qc.py
-----------------------
Compute pixel counts by category for final disturbance rasters and flag
potential issues/inconsistencies.

Expected categories (byte):
  0   = background
  1-4 = harvest severity
  5   = insect/disease
  10  = fire
"""

import argparse
import csv
import glob
import logging
import os
from collections import defaultdict

import arcpy

import disturbance_config as cfg

ALLOWED_VALUES = {0, 1, 2, 3, 4, 5, 10}
HARVEST_VALUES = {1, 2, 3, 4}


def _exists(path: str) -> bool:
    return bool(path) and (arcpy.Exists(path) or os.path.exists(path))


def _set_env(ds: str) -> None:
    if not _exists(ds):
        raise FileNotFoundError(f"Cannot set env; dataset not found: {ds}")
    d = arcpy.Describe(ds)
    arcpy.env.snapRaster = ds
    arcpy.env.cellSize = ds
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference


def _parse_disturb_name(path: str) -> tuple[str | None, str | None]:
    """
    Parse disturb_<method>_<period>.tif into (method, period).
    Returns (None, None) if the filename does not match the expected pattern.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    parts = name.split("_")
    if len(parts) < 3 or parts[0] != "disturb":
        return None, None
    method = parts[1]
    period = "_".join(parts[2:])
    return method, period


def _build_attribute_table(raster_path: str) -> None:
    try:
        arcpy.management.BuildRasterAttributeTable(raster_path, "OVERWRITE")
    except arcpy.ExecuteError:
        msg = arcpy.GetMessages(2)
        logging.warning("Failed to build attribute table for %s: %s", raster_path, msg)
    except Exception as exc:
        logging.warning("Failed to build attribute table for %s: %s", raster_path, exc)


def _value_counts(raster_path: str) -> dict[int, int]:
    _build_attribute_table(raster_path)
    counts: dict[int, int] = {}
    try:
        with arcpy.da.SearchCursor(raster_path, ["VALUE", "COUNT"]) as cursor:
            for value, count in cursor:
                if value is None:
                    continue
                counts[int(value)] = int(count)
    except arcpy.ExecuteError:
        msg = arcpy.GetMessages(2)
        raise RuntimeError(f"Failed to read attribute table for {raster_path}: {msg}") from None
    return counts


def _summarize_raster(raster_path: str) -> dict:
    method, period = _parse_disturb_name(raster_path)
    counts = _value_counts(raster_path)
    total = sum(counts.values())

    unexpected = sorted(v for v in counts if v not in ALLOWED_VALUES)
    harvest_total = sum(counts.get(v, 0) for v in HARVEST_VALUES)
    insect_total = counts.get(5, 0)
    fire_total = counts.get(10, 0)

    issues: list[str] = []
    if unexpected:
        issues.append(f"unexpected values: {unexpected}")
    if total == 0:
        issues.append("no counted pixels (total=0)")
    if harvest_total == 0:
        issues.append("no harvest pixels (1-4) present")
    if insect_total == 0:
        issues.append("no insect pixels (5) present")
    if fire_total == 0:
        issues.append("no fire pixels (10) present")

    return {
        "path": raster_path,
        "method": method,
        "period": period,
        "counts": counts,
        "total": total,
        "issues": issues,
    }


def _write_csv(rows: list[dict], out_csv: str) -> None:
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["raster", "method", "period", "value", "count", "percent"])
        for row in rows:
            total = row["total"] or 1
            for value, count in sorted(row["counts"].items()):
                percent = (count / total) * 100.0
                writer.writerow([
                    row["path"],
                    row["method"],
                    row["period"],
                    value,
                    count,
                    f"{percent:.4f}",
                ])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute per-category pixel counts for final disturbance rasters."
    )
    parser.add_argument(
        "--input-dir",
        default=cfg.NLCD_FINAL_DIR,
        help="Directory containing final disturbance rasters (default: NLCD_FINAL_DIR).",
    )
    parser.add_argument(
        "--pattern",
        default="disturb_*.tif",
        help="Glob pattern for disturbance rasters (default: disturb_*.tif).",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional CSV output path for per-value counts.",
    )
    args = parser.parse_args()

    logging.info("Starting final disturbance QC.")

    if not _exists(cfg.NLCD_RASTER):
        raise FileNotFoundError(f"NLCD_RASTER not found: {cfg.NLCD_RASTER}")
    _set_env(cfg.NLCD_RASTER)

    search_glob = os.path.join(args.input_dir, args.pattern)
    rasters = sorted(glob.glob(search_glob))
    if not rasters:
        logging.error("No rasters found with pattern: %s", search_glob)
        return 1

    expected_periods = set(cfg.TIME_PERIODS_ALL.keys())
    found_periods: dict[str, set[str]] = defaultdict(set)
    summaries: list[dict] = []

    for raster_path in rasters:
        logging.info("Summarizing %s", raster_path)
        summary = _summarize_raster(raster_path)
        summaries.append(summary)

        method = summary["method"]
        period = summary["period"]
        if method and period:
            found_periods[method].add(period)
        else:
            logging.warning("Unrecognized filename pattern: %s", raster_path)

        counts = summary["counts"]
        total = summary["total"] or 1
        for value in sorted(counts):
            count = counts[value]
            logging.info(
                "  value=%s count=%s (%.2f%%)",
                value,
                count,
                (count / total) * 100.0,
            )
        if summary["issues"]:
            logging.warning("  Issues: %s", "; ".join(summary["issues"]))

    for method, periods in sorted(found_periods.items()):
        missing = sorted(expected_periods - periods)
        extras = sorted(periods - expected_periods)
        if missing:
            logging.warning("Method '%s' missing %d period(s): %s", method, len(missing), ", ".join(missing))
        if extras:
            logging.warning("Method '%s' has unexpected period(s): %s", method, ", ".join(extras))

    if args.output_csv:
        _write_csv(summaries, args.output_csv)
        logging.info("Wrote CSV summary: %s", args.output_csv)

    logging.info("Final disturbance QC completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())