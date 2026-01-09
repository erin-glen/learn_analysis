#!/usr/bin/env python3
"""
fire_qc_2016_2019.py
--------------------
Targeted QC for the 2016-2019 fire pipeline:
  * Raw MTBS rasters for each year in the period
  * Yearly reclassified rasters
  * Period-combined fire raster
  * Final fire presence raster
  * Final combined disturbance rasters

The goal is to isolate where "no burned pixels" is introduced.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Iterable

import arcpy

import disturbance_config as cfg

RAW_BURNED_VALUES = {1, 2, 3, 4, 5}
RAW_ALL_VALUES = RAW_BURNED_VALUES | {6}
RECLASS_VALUES = {0, 3, 10}
FIRE_PRESENCE_VALUES = {0, 10}
FINAL_VALUES = {0, 1, 2, 3, 4, 5, 10}


def _exists(path: str) -> bool:
    return bool(path) and (arcpy.Exists(path) or os.path.exists(path))


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
    with arcpy.da.SearchCursor(raster_path, ["VALUE", "COUNT"]) as cursor:
        for value, count in cursor:
            if value is None:
                continue
            counts[int(value)] = int(count)
    return counts


def _sum_counts(counts: dict[int, int], values: Iterable[int]) -> int:
    return sum(counts.get(v, 0) for v in values)


def _log_counts(label: str, counts: dict[int, int]) -> None:
    for value in sorted(counts):
        logging.info("  %s value=%s count=%s", label, value, counts[value])


def _grid_signature(path: str) -> dict[str, str]:
    d = arcpy.Describe(path)
    sr = getattr(d, "spatialReference", None)
    extent = getattr(d, "extent", None)
    return {
        "width": str(getattr(d, "width", "?")),
        "height": str(getattr(d, "height", "?")),
        "cell_x": f"{getattr(d, 'meanCellWidth', '?')}",
        "cell_y": f"{getattr(d, 'meanCellHeight', '?')}",
        "sr": getattr(sr, "name", "Unknown") if sr else "Unknown",
        "extent": str(extent) if extent else "Unknown",
    }


def _log_grid(tag: str, path: str) -> None:
    try:
        sig = _grid_signature(path)
        logging.info(
            "  %s grid: size=(%s x %s), cell=%s x %s, SR=%s, extent=%s",
            tag,
            sig["width"],
            sig["height"],
            sig["cell_x"],
            sig["cell_y"],
            sig["sr"],
            sig["extent"],
        )
    except Exception as exc:
        logging.warning("  %s grid: unable to read (%s)", tag, exc)


def _warn_if_misaligned(path: str, ref_path: str, label: str) -> None:
    try:
        dp = arcpy.Describe(path)
        dr = arcpy.Describe(ref_path)
        sr_p = getattr(dp, "spatialReference", None)
        sr_r = getattr(dr, "spatialReference", None)
        cw_p, ch_p = dp.meanCellWidth, dp.meanCellHeight
        cw_r, ch_r = dr.meanCellWidth, dr.meanCellHeight
        if (not sr_p or not sr_r) or (sr_p.name != sr_r.name) or (abs(cw_p - cw_r) > 1e-6) or (abs(ch_p - ch_r) > 1e-6):
            logging.warning(
                "  Potential misalignment for %s:\n"
                "    %s (cell %.6f x %.6f, SR=%s)\n"
                "    REF=%s (cell %.6f x %.6f, SR=%s)",
                label,
                path,
                cw_p,
                ch_p,
                getattr(sr_p, "name", "?"),
                ref_path,
                cw_r,
                ch_r,
                getattr(sr_r, "name", "?"),
            )
    except Exception as exc:
        logging.warning("  Could not compare grid for %s (%s)", label, exc)


def _find_raw_mtbs_raster(year: int) -> str | None:
    year_str = str(year)
    folder = f"mtbs_CONUS_{year}"
    filename = f"mtbs_CONUS_{year}.tif"

    bases = []
    if getattr(cfg, "FIRE_RAW_DIR", ""):
        bases.append(cfg.FIRE_RAW_DIR)
    if getattr(cfg, "FIRE_ROOT", ""):
        bases.append(cfg.FIRE_ROOT)
        bases.append(os.path.join(cfg.FIRE_ROOT, "Raw"))

    candidates = [os.path.join(b, year_str, folder, filename) for b in bases]
    for path in candidates:
        if arcpy.Exists(path):
            return path

    logging.warning(
        "Raw MTBS raster for year=%s not found. Checked:\n  - %s",
        year,
        "\n  - ".join(candidates),
    )
    return None


def _qc_raw_year(year: int) -> None:
    logging.info("Raw MTBS QC for year=%s", year)
    raw_path = _find_raw_mtbs_raster(year)
    if not raw_path:
        logging.warning("  Missing raw raster for %s", year)
        return

    counts = _value_counts(raw_path)
    total = sum(counts.values())
    burned = _sum_counts(counts, RAW_BURNED_VALUES)
    unexpected = sorted(v for v in counts if v not in RAW_ALL_VALUES)

    logging.info("  Raw path: %s", raw_path)
    _log_grid("RAW", raw_path)
    logging.info("  Raw total pixels: %s", total)
    logging.info("  Raw burned pixels (1-5): %s", burned)
    if unexpected:
        logging.warning("  Raw unexpected values: %s", unexpected)
    if burned == 0:
        logging.warning("  Raw burned pixels are zero -> issue likely in MTBS data for %s", year)

    _log_counts("RAW", counts)


def _qc_reclass_year(year: int) -> None:
    reclass_path = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif")
    logging.info("Reclass QC for year=%s", year)
    if not _exists(reclass_path):
        logging.warning("  Missing reclassified raster: %s", reclass_path)
        return

    counts = _value_counts(reclass_path)
    total = sum(counts.values())
    fire_pixels = _sum_counts(counts, {3, 10})
    unexpected = sorted(v for v in counts if v not in RECLASS_VALUES)

    logging.info("  Reclass path: %s", reclass_path)
    _log_grid("RECLASS", reclass_path)
    if _exists(cfg.NLCD_RASTER):
        _warn_if_misaligned(reclass_path, cfg.NLCD_RASTER, f"reclass {year}")
    logging.info("  Reclass total pixels: %s", total)
    logging.info("  Reclass fire pixels (3/10): %s", fire_pixels)
    if unexpected:
        logging.warning("  Reclass unexpected values: %s", unexpected)
    if fire_pixels == 0:
        logging.warning("  Reclass fire pixels are zero -> issue likely in reclassification for %s", year)

    _log_counts("RECLASS", counts)


def _qc_period_outputs(period: str) -> None:
    combined_path = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period}.tif")
    logging.info("Period fire QC for %s", period)
    if not _exists(combined_path):
        logging.warning("  Missing combined fire raster: %s", combined_path)
        return

    counts = _value_counts(combined_path)
    fire_pixels = _sum_counts(counts, {3, 10})
    unexpected = sorted(v for v in counts if v not in RECLASS_VALUES)

    logging.info("  Combined fire path: %s", combined_path)
    _log_grid("COMBINED", combined_path)
    if _exists(cfg.NLCD_RASTER):
        _warn_if_misaligned(combined_path, cfg.NLCD_RASTER, f"combined {period}")
    logging.info("  Combined fire pixels (3/10): %s", fire_pixels)
    if unexpected:
        logging.warning("  Combined fire unexpected values: %s", unexpected)
    if fire_pixels == 0:
        logging.warning("  Combined fire pixels are zero -> issue likely after reclass combine.")
        logging.warning(
            "  Inspect combine inputs: check if yearly reclass rasters align to NLCD "
            "and ensure fire.py uses NLCD env during CellStatistics."
        )

    _log_counts("COMBINED", counts)


def _qc_final_fire_presence(period: str) -> None:
    presence_path = os.path.join(cfg.NLCD_FINAL_FIRE_DIR, f"fire_{period}.tif")
    logging.info("Final fire presence QC for %s", period)
    if not _exists(presence_path):
        logging.warning("  Missing final fire presence raster: %s", presence_path)
        return

    counts = _value_counts(presence_path)
    fire_pixels = _sum_counts(counts, {10})
    unexpected = sorted(v for v in counts if v not in FIRE_PRESENCE_VALUES)

    logging.info("  Final fire presence path: %s", presence_path)
    _log_grid("FINAL_FIRE", presence_path)
    if _exists(cfg.NLCD_RASTER):
        _warn_if_misaligned(presence_path, cfg.NLCD_RASTER, f"final fire {period}")
    logging.info("  Final fire presence pixels (10): %s", fire_pixels)
    if unexpected:
        logging.warning("  Final fire presence unexpected values: %s", unexpected)
    if fire_pixels == 0:
        logging.warning("  Final fire presence pixels are zero -> issue likely in final_disturbance fire masking.")

    _log_counts("FINAL_FIRE", counts)


def _final_method_tags() -> list[str]:
    workflows = getattr(cfg, "FINAL_HARVEST_WORKFLOWS", [getattr(cfg, "HARVEST_WORKFLOW", "")])
    tags: list[str] = []
    for wf in workflows:
        try:
            tag = cfg.harvest_product_config(wf).get("method_tag", "abs")
        except Exception:
            continue
        if tag not in tags:
            tags.append(tag)
    return tags or ["abs"]


def _qc_final_disturbance(period: str) -> None:
    logging.info("Final disturbance QC for %s", period)
    final_dir = cfg.final_combined_dir()
    for tag in _final_method_tags():
        path = os.path.join(final_dir, f"disturb_{tag}_{period}.tif")
        if not _exists(path):
            logging.warning("  Missing final disturbance raster: %s", path)
            continue

        counts = _value_counts(path)
        fire_pixels = _sum_counts(counts, {10})
        unexpected = sorted(v for v in counts if v not in FINAL_VALUES)

        logging.info("  Disturbance path: %s", path)
        _log_grid(f"DISTURB[{tag}]", path)
        if _exists(cfg.NLCD_RASTER):
            _warn_if_misaligned(path, cfg.NLCD_RASTER, f"disturbance {tag} {period}")
        logging.info("  Disturbance fire pixels (10): %s", fire_pixels)
        if unexpected:
            logging.warning("  Disturbance unexpected values: %s", unexpected)
        if fire_pixels == 0:
            logging.warning("  Disturbance fire pixels are zero -> issue likely after final combine.")

        _log_counts(f"DISTURB[{tag}]", counts)


def _resolve_period_years(period: str, years_arg: list[int] | None) -> list[int]:
    if years_arg:
        return years_arg
    if hasattr(cfg, "FIRE_TIME_PERIODS") and period in cfg.FIRE_TIME_PERIODS:
        return list(cfg.FIRE_TIME_PERIODS[period])
    if hasattr(cfg, "TIME_PERIODS_ALL") and period in cfg.TIME_PERIODS_ALL:
        a, b = cfg.TIME_PERIODS_ALL[period][0], cfg.TIME_PERIODS_ALL[period][-1]
        return list(range(int(a), int(b) + 1))
    raise ValueError(f"Period '{period}' not found in config; pass --years explicitly.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QC the fire processing chain for the 2016-2019 period."
    )
    parser.add_argument(
        "--period",
        default="2016_2019",
        help="Fire period label to QC (default: 2016_2019).",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=None,
        help="Explicit list of years to QC (overrides period lookup).",
    )
    args = parser.parse_args()

    logging.info("Starting fire QC for period=%s", args.period)

    years = _resolve_period_years(args.period, args.years)
    logging.info("Years resolved for period %s: %s", args.period, years)

    for year in years:
        _qc_raw_year(year)
        _qc_reclass_year(year)

    _qc_period_outputs(args.period)
    _qc_final_fire_presence(args.period)
    _qc_final_disturbance(args.period)

    logging.info("Fire QC completed for %s.", args.period)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
