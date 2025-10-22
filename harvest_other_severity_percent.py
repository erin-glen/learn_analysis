#!/usr/bin/env python3
"""
Derives harvest/other disturbances using NLCD Tree Canopy Cover (TCC) rasters,
classifying canopy loss severity based on percent change relative to the start
year canopy.

This script mirrors ``harvest_other_severity.py`` but computes percent change
instead of absolute canopy change. It produces both the percent change raster
and the associated severity raster for each configured period.

Percent change is only computed where both start and end pixels are valid and
where the start-year canopy value is greater than zero to avoid division by
zero. Results are clipped to the range [-100, 100] to guard against floating
point artifacts and ensure the loss magnitude is bounded by 100%.
"""

import os
import logging
import arcpy
import argparse
from typing import Iterable, Sequence

import disturbance_config as cfg


def _exists(path: str) -> bool:
    return bool(path) and (arcpy.Exists(path) or os.path.exists(path))


def _tcc_path_or_none(year: int):
    p = cfg.NLCD_TCC_RASTERS.get(year)
    return p if _exists(p) else None


def _set_env_from_dataset(ds_path: str):
    if not _exists(ds_path):
        raise FileNotFoundError(f"Cannot set env; dataset not found: {ds_path}")
    desc = arcpy.Describe(ds_path)
    arcpy.env.snapRaster = ds_path
    arcpy.env.cellSize = ds_path
    arcpy.env.extent = desc.extent
    arcpy.env.outputCoordinateSystem = desc.spatialReference
    logging.info("ArcPy env set from: %s", ds_path)


def _classify_loss(loss_raster):
    breaks = cfg.NLCD_TCC_SEVERITY_BREAKS
    if len(breaks) != 4:
        raise ValueError("NLCD_TCC_SEVERITY_BREAKS must have four class upper-bounds.")
    b1, b2, b3, b4 = sorted(breaks)
    from arcpy.sa import Con  # after CheckOutExtension
    return Con(
        loss_raster <= 0, 0,
        Con(
            loss_raster <= b1, 1,
            Con(
                loss_raster <= b2, 2,
                Con(
                    loss_raster <= b3, 3,
                    Con(loss_raster <= b4, 4, 4),
                ),
            ),
        ),
    )


def _parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Derive harvest/other severity rasters from NLCD Tree Canopy Cover "
            "percent change. Tile filters are accepted for parity with the "
            "Hansen workflow but will be ignored because the rasters are not tiled."
        )
    )
    parser.add_argument(
        "--tile-id",
        dest="tile_ids",
        action="append",
        default=[],
        metavar="TILE_ID",
        help="Optional Hansen-style tile ids (ignored, logged for awareness).",
    )
    parser.add_argument(
        "--tiles",
        dest="tile_csv",
        metavar="ID1,ID2",
        help="Comma-separated list of tile ids (ignored, logged).",
    )
    return parser.parse_args(argv)


def _combine_tile_args(tile_ids: Iterable[str], csv: str | None) -> list[str]:
    combined = list(tile_ids) if tile_ids else []
    if csv:
        combined.extend(part.strip() for part in csv.split(",") if part.strip())
    return combined


def main(tile_ids: Iterable[str] | None = None):
    logging.info("Starting NLCD Tree Canopy percent change harvest/other processing.")
    tile_ids = list(tile_ids) if tile_ids else []
    if tile_ids:
        logging.info(
            "Tile filter requested (%s) but NLCD TCC rasters are not tiled; proceeding with full extent.",
            ", ".join(tile_ids),
        )

    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")
    try:
        # Prefer the NLCD LC 2021 reference if present
        if _exists(cfg.NLCD_RASTER):
            _set_env_from_dataset(cfg.NLCD_RASTER)
        else:
            logging.warning("NLCD_RASTER missing; will set env per-period from TCC.")

        from arcpy.sa import Abs, Con, Float, Raster, SetNull, IsNull

        # -------- constants for validity --------
        VALID_MIN = 0
        VALID_MAX = 100

        for period_name, years in cfg.TIME_PERIODS.items():
            if not years:
                logging.warning("Skipping '%s' (no years provided).", period_name)
                continue

            start_year = min(years)
            end_year = max(years)
            if start_year == end_year:
                logging.warning("Skipping '%s' (start==end==%s).", period_name, start_year)
                continue

            start_path = _tcc_path_or_none(start_year)
            end_path = _tcc_path_or_none(end_year)

            missing = [y for y, p in [(start_year, start_path), (end_year, end_path)] if not p]
            if missing:
                logging.error(
                    "Skipping '%s' — missing TCC raster(s) for year(s): %s. "
                    "Configured paths: start=%s | end=%s",
                    period_name, missing, cfg.NLCD_TCC_RASTERS.get(start_year), cfg.NLCD_TCC_RASTERS.get(end_year)
                )
                continue

            # If no global reference was set, set env from the end-year TCC now
            if not _exists(cfg.NLCD_RASTER):
                _set_env_from_dataset(end_path)

            out_change = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_pct_change_{period_name}.tif")
            out_severity = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_pct_severity_{period_name}.tif")

            logging.info(
                "Processing '%s' with start=%s end=%s", period_name, start_year, end_year
            )
            logging.info("  Start TCC => %s", start_path)
            logging.info("  End   TCC => %s", end_path)

            # ----- load rasters -----
            start_r = Raster(start_path)
            end_r = Raster(end_path)

            # ----- mask to valid range and nulls BEFORE math -----
            # invalid if null OR <0 OR >100
            invalid_start = IsNull(start_r) | (start_r < VALID_MIN) | (start_r > VALID_MAX)
            invalid_end   = IsNull(end_r)   | (end_r   < VALID_MIN) | (end_r   > VALID_MAX)

            # additionally require start > 0 for percent change calculations
            start_nonpositive = start_r <= 0

            start_valid = SetNull(invalid_start | start_nonpositive, start_r)  # NoData where invalid or zero
            end_valid   = SetNull(invalid_end,   end_r)

            # only compute percent change where BOTH are valid
            percent_change = SetNull(
                IsNull(start_valid) | IsNull(end_valid),
                Float((end_valid - start_valid) / start_valid * 100)
            )

            # clip to [-100, 100] to guard against floating point artifacts
            percent_change = Con(percent_change < -100, -100,
                                 Con(percent_change > 100, 100, percent_change))

            # ----- save percent change (always overwrite) -----
            percent_change.save(out_change)
            logging.info("Saved canopy percent change raster => %s", out_change)

            # quick sanity check on range after save
            try:
                arcpy.management.CalculateStatistics(out_change)
                mn = float(arcpy.management.GetRasterProperties(out_change, "MINIMUM").getOutput(0))
                mx = float(arcpy.management.GetRasterProperties(out_change, "MAXIMUM").getOutput(0))
                logging.info("Range for %s percent change: min=%s max=%s", period_name, mn, mx)
            except Exception as e:
                logging.warning("Could not compute stats for %s: %s", out_change, e)

            # ----- derive loss magnitude and severity -----
            loss_pct = Con(percent_change < 0, Abs(percent_change), 0)
            severity_r = _classify_loss(loss_pct)

            # save severity (always overwrite)
            severity_r.save(out_severity)
            logging.info("Saved canopy loss percent severity raster => %s", out_severity)

        logging.info("Completed NLCD TCC percent change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")


if __name__ == "__main__":
    args = _parse_cli_args()
    combined_tiles = _combine_tile_args(args.tile_ids, args.tile_csv)
    main(tile_ids=combined_tiles)
