#!/usr/bin/env python3
"""
Derives harvest/other disturbances using NLCD Tree Canopy Cover (TCC) rasters.

For each configured period, the script:
1) Loads the earliest and latest TCC rasters for the interval.
2) Computes canopy change (end - start) and isolates canopy loss.
3) Classifies loss magnitude into four severity classes (0-4) based on
   configuration thresholds.

This version prefers the NLCD Land Cover (2021) raster as the global reference
for ArcPy env; if missing, it will fall back to the period's end-year TCC raster.
"""

import os
import logging
import arcpy

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


def main():
    logging.info("Starting NLCD Tree Canopy change-based harvest/other processing.")
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")
    try:
        # Prefer the NLCD LC 2021 reference if present
        if _exists(cfg.NLCD_RASTER):
            _set_env_from_dataset(cfg.NLCD_RASTER)
        else:
            logging.warning("NLCD_RASTER missing; will set env per-period from TCC.")

        from arcpy.sa import Abs, Con, Raster

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

            out_change = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_change_{period_name}.tif")
            out_severity = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_severity_{period_name}.tif")

            if arcpy.Exists(out_severity):
                logging.info("Severity exists for '%s' => %s. Skipping.", period_name, out_severity)
                continue

            logging.info(
                "Processing '%s' with start=%s end=%s", period_name, start_year, end_year
            )
            logging.info("  Start TCC => %s", start_path)
            logging.info("  End   TCC => %s", end_path)

            start_r = Raster(start_path)
            end_r = Raster(end_path)
            change_r = end_r - start_r

            if arcpy.Exists(out_change):
                logging.info("Change exists for '%s' => %s. Overwriting in memory only.", period_name, out_change)
            else:
                change_r.save(out_change)
                logging.info("Saved change => %s", out_change)

            loss_r = Con(change_r < 0, Abs(change_r), 0)
            severity_r = _classify_loss(loss_r)
            severity_r.save(out_severity)
            logging.info("Saved severity => %s", out_severity)

        logging.info("Completed NLCD TCC change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")


if __name__ == "__main__":
    main()
