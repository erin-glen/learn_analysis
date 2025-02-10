# final_disturbance.py
"""
Combines fire, insect, and harvest rasters via CellStatistics (MAX)
and optionally masks out low-severity fire.
"""

import arcpy
from arcpy.sa import *
import os
import logging

import disturbance_config as cfg

def main():
    """
    For each time period:
    1. Finds fire_{period}, insect_damage_{period}, hansen_{period}
    2. Combines them with 'MAX'
    3. (Optional) Reclass if you want to mask low severity.
    4. Saves the final combined raster => disturb_{period}.tif
    """
    logging.info("Starting final_disturbance.py...")

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER
    arcpy.env.overwriteOutput = True

    for period in cfg.TIME_PERIODS.keys():
        # Build paths
        fire_path = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period}.tif")
        insect_path = os.path.join(cfg.INSECT_FINAL_DIR, f"insect_damage_{period}.tif")
        hansen_path = os.path.join(cfg.HANSEN_OUTPUT_DIR, f"hansen_{period}.tif")

        if not (arcpy.Exists(fire_path) and
                arcpy.Exists(insect_path) and
                arcpy.Exists(hansen_path)):
            logging.warning(f"Missing one or more rasters for period={period}. Skipping.")
            continue

        logging.info(f"Combining Fire={fire_path}, Insect={insect_path}, Harvest={hansen_path}")

        # Max combination
        fire_ras = Raster(fire_path)
        insect_ras = Raster(insect_path)
        hansen_ras = Raster(hansen_path)

        combined_max = CellStatistics([fire_ras, insect_ras, hansen_ras], "MAXIMUM", "DATA")

        # Example: mask out code=3 => 0 (low severity fire).
        # If you do NOT want to mask out code=3, skip this step.
        final_dist = Con(combined_max == 3, 0, combined_max)

        # Save
        out_raster_path = os.path.join(cfg.FINAL_COMBINED_DIR, f"disturb_{period}.tif")
        final_dist.save(out_raster_path)
        logging.info(f"Final combined disturbance => {out_raster_path}")

    arcpy.CheckInExtension("Spatial")
    logging.info("All time periods processed successfully.")

if __name__ == "__main__":
    main()
