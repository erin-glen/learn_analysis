# fire.py
"""
Processes MTBS fire data in two stages:

1) Year-by-Year Reclassification:
   - For each unique year in the config's TIME_PERIODS, read raw MTBS raster
     (mtbs_CONUS_{year}.tif), reclassify codes (1,2,5->3; 3,4->10; 6 or NoData->0),
     and save output as fire_{year}_reclass.tif.

2) Multi-Year Combination:
   - For each time period in cfg.TIME_PERIODS, gather the reclassified rasters
     for each year in that period, perform CellStatistics (MAX), and save
     the final as fire_{period}.tif.

Example
-------
```
python fire.py
```
"""

import arcpy
from arcpy.sa import *
import os
import logging

import disturbance_config as cfg

def main():
    """
    Main driver for the new 2-step fire processing:
      (A) Reclassify each unique year
      (B) Combine reclassified rasters per time period
    """
    logging.info("Starting fire.py with Year-by-Year Reclassification + Period Combination.")

    # Set up ArcPy environment
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER
    arcpy.env.overwriteOutput = True

    # ---------------------------------------------------------------
    # A. Year-by-Year Reclassification
    # ---------------------------------------------------------------
    # Gather all unique years from the config's TIME_PERIODS
    all_years = set()
    for period_name, year_list in cfg.TIME_PERIODS.items():
        all_years.update(year_list)
    all_years = sorted(all_years)

    logging.info(f"Unique fire years found in config TIME_PERIODS: {all_years}")

    for year in all_years:
        # Paths
        raw_tif = os.path.join(cfg.FIRE_ROOT, str(year), f"mtbs_CONUS_{year}", f"mtbs_CONUS_{year}.tif")
        reclass_tif = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif")

        # If reclass file exists, skip
        if arcpy.Exists(reclass_tif):
            logging.info(f"Year={year} reclassified file already exists => {reclass_tif}. Skipping reclassification.")
            continue

        # Check if raw input exists
        if not arcpy.Exists(raw_tif):
            logging.warning(f"Raw MTBS raster for year={year} not found => {raw_tif}. Skipping reclassification.")
            continue

        logging.info(f"Reclassifying raw MTBS => {reclass_tif}")

        # 1) Load raw
        raw_raster = Raster(raw_tif)

        # 2) Reclassify codes:
        #    1,2,5 => 3 (Low severity),
        #    3,4 => 10 (Mod/High),
        #    6 or NoData => 0 (Unburned)
        reclass_map = RemapValue([
            [1, 3],
            [2, 3],
            [3, 10],
            [4, 10],
            [5, 3],
            [6, 0]
        ])
        rc_temp = Reclassify(raw_raster, "Value", reclass_map, "NODATA")
        year_reclass = Con(IsNull(rc_temp), 0, rc_temp)

        # 3) Save
        year_reclass.save(reclass_tif)
        logging.info(f"Year={year} reclassified fire saved => {reclass_tif}")

    # ---------------------------------------------------------------
    # B. Combine Reclassified Rasters Per Time Period
    # ---------------------------------------------------------------
    for period_name, year_list in cfg.TIME_PERIODS.items():
        out_final_raster = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period_name}.tif")

        # If final output exists, skip
        if arcpy.Exists(out_final_raster):
            logging.info(f"Multi-year fire raster for '{period_name}' already exists => {out_final_raster}. Skipping.")
            continue

        # Collect reclassified rasters for each year in this period
        reclass_paths = []
        for year in year_list:
            reclass_tif = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif")
            if arcpy.Exists(reclass_tif):
                reclass_paths.append(reclass_tif)
            else:
                logging.warning(f"No reclass file found for year={year}, skipping it.")

        if not reclass_paths:
            logging.warning(f"No reclassified rasters found for period='{period_name}'. Skipping.")
            continue

        # Perform CellStatistics (MAX) to combine
        logging.info(f"Combining {len(reclass_paths)} yearly reclassified rasters with MAX => {out_final_raster}")
        ras_objs = [Raster(p) for p in reclass_paths]
        combined_max = CellStatistics(ras_objs, "MAXIMUM", "DATA")

        # Save final
        combined_max.save(out_final_raster)
        logging.info(f"Final multi-year fire raster saved => {out_final_raster}")

    # Cleanup
    arcpy.CheckInExtension("Spatial")
    logging.info("Fire processing (yearly reclassification + period combination) completed successfully.")


if __name__ == "__main__":
    main()