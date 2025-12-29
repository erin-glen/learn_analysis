#!/usr/bin/env python3
# fire.py

"""
Processes MTBS fire data in two stages:

1) Year-by-Year Reclassification:
   - For each unique year in the chosen periods, read raw MTBS raster
     (mtbs_CONUS_{year}.tif), reclassify codes (1,2,5->3; 3,4->10; 6 or NoData->0),
     and save output as fire_{year}_reclass.tif.

2) Multi-Year Combination:
   - For each time period, gather the reclassified rasters for ALL years in that
     period, perform CellStatistics (MAX), and save the final as fire_{period}.tif.

Raw input layout (expected)
---------------------------
<cfg.FIRE_RAW_DIR>\<year>\mtbs_CONUS_<year>\mtbs_CONUS_<year>.tif

Example:
C:\GIS\Data\LEARN\Disturbances\Fire\Raw\2001\mtbs_CONUS_2001\mtbs_CONUS_2001.tif
"""

import os
import logging

import arcpy
from arcpy.sa import *

import disturbance_config as cfg


def _get_fire_periods() -> dict:
    """
    Prefer fire-specific periods if provided by config; otherwise fall back.
    Expected shape: { "2011_2013": [2011, 2012, 2013], ... }
    """
    if hasattr(cfg, "FIRE_TIME_PERIODS") and cfg.FIRE_TIME_PERIODS:
        logging.info("Using cfg.FIRE_TIME_PERIODS for fire processing.")
        return cfg.FIRE_TIME_PERIODS

    # Fallback: attempt to expand cfg.TIME_PERIODS endpoint pairs into ranges
    if hasattr(cfg, "TIME_PERIODS") and cfg.TIME_PERIODS:
        sample = next(iter(cfg.TIME_PERIODS.values()))
        # If the lists are already long-ish, assume they are year lists.
        if isinstance(sample, list) and len(sample) > 2:
            logging.info("Using cfg.TIME_PERIODS directly (appears to contain year lists).")
            return cfg.TIME_PERIODS

        # Otherwise, treat them as endpoints [a, b] and expand inclusively.
        expanded = {}
        for period_name, years in cfg.TIME_PERIODS.items():
            if not years or len(years) < 2:
                continue
            a, b = int(years[0]), int(years[-1])
            expanded[period_name] = list(range(a, b + 1))
        logging.info("Using expanded cfg.TIME_PERIODS endpoint pairs for fire processing.")
        return expanded

    raise ValueError("No fire periods found. Define cfg.FIRE_TIME_PERIODS or cfg.TIME_PERIODS.")


def _find_raw_mtbs_raster(year: int) -> str | None:
    """
    Find the MTBS raster for a given year using the expected folder convention.
    """
    year_str = str(year)
    folder = f"mtbs_CONUS_{year}"
    filename = f"mtbs_CONUS_{year}.tif"

    # Prefer FIRE_RAW_DIR when available; otherwise try FIRE_ROOT and FIRE_ROOT\Raw
    bases = []
    if hasattr(cfg, "FIRE_RAW_DIR") and cfg.FIRE_RAW_DIR:
        bases.append(cfg.FIRE_RAW_DIR)
    if hasattr(cfg, "FIRE_ROOT") and cfg.FIRE_ROOT:
        bases.append(cfg.FIRE_ROOT)
        bases.append(os.path.join(cfg.FIRE_ROOT, "Raw"))

    candidates = [os.path.join(b, year_str, folder, filename) for b in bases]

    for p in candidates:
        if arcpy.Exists(p):
            return p

    logging.warning(
        f"Raw MTBS raster for year={year} not found. Checked:\n  - " + "\n  - ".join(candidates)
    )
    return None


def main():
    logging.info("Starting fire.py with Year-by-Year Reclassification + Period Combination.")

    # Set up ArcPy environment
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER
    arcpy.env.overwriteOutput = True

    periods = _get_fire_periods()

    # ---------------------------------------------------------------
    # A. Year-by-Year Reclassification
    # ---------------------------------------------------------------
    all_years = sorted({y for years in periods.values() for y in years})
    logging.info(f"Unique fire years to process: {all_years}")

    for year in all_years:
        reclass_tif = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif")

        if arcpy.Exists(reclass_tif):
            logging.info(f"Year={year} reclassified file already exists => {reclass_tif}. Skipping reclassification.")
            continue

        raw_tif = _find_raw_mtbs_raster(year)
        if raw_tif is None:
            continue

        logging.info(f"Reclassifying raw MTBS ({raw_tif}) => {reclass_tif}")

        raw_raster = Raster(raw_tif)

        reclass_map = RemapValue([
            [1, 3],
            [2, 3],
            [3, 10],
            [4, 10],
            [5, 3],
            [6, 0],
        ])

        rc_temp = Reclassify(raw_raster, "Value", reclass_map, "NODATA")
        year_reclass = Con(IsNull(rc_temp), 0, rc_temp)

        year_reclass.save(reclass_tif)
        logging.info(f"Year={year} reclassified fire saved => {reclass_tif}")

    # ---------------------------------------------------------------
    # B. Combine Reclassified Rasters Per Time Period (require completeness)
    # ---------------------------------------------------------------
    for period_name, year_list in periods.items():
        out_final_raster = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period_name}.tif")

        if arcpy.Exists(out_final_raster):
            logging.info(f"Multi-year fire raster for '{period_name}' already exists => {out_final_raster}. Skipping.")
            continue

        reclass_paths = []
        missing_years = []

        for year in year_list:
            reclass_tif = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif")
            if arcpy.Exists(reclass_tif):
                reclass_paths.append(reclass_tif)
            else:
                missing_years.append(year)

        if missing_years:
            logging.warning(
                f"Period='{period_name}' missing reclass years {missing_years}. "
                f"Skipping to avoid a partial period output."
            )
            continue

        logging.info(f"Combining {len(reclass_paths)} yearly reclassified rasters with MAX => {out_final_raster}")
        ras_objs = [Raster(p) for p in reclass_paths]
        combined_max = CellStatistics(ras_objs, "MAXIMUM", "DATA")

        combined_max.save(out_final_raster)
        logging.info(f"Final multi-year fire raster saved => {out_final_raster}")

    arcpy.CheckInExtension("Spatial")
    logging.info("Fire processing completed successfully.")


if __name__ == "__main__":
    main()
