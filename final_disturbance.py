# final_disturbance.py
"""
Combines fire, insect, and harvest rasters via CellStatistics (MAX)
and optionally masks out low-severity fire. The harvest raster source is
controlled by disturbance_config.HARVEST_WORKFLOW so either the legacy
Hansen workflow or the newer NLCD Tree Canopy Cover severity workflow can
be used without code changes.

Example
-------
```
python final_disturbance.py
```
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

    harvest_cfg = cfg.harvest_product_config()
    logging.info(
        "Using harvest workflow '%s' => %s",
        cfg.HARVEST_WORKFLOW,
        harvest_cfg.get("description", ""),
    )
    logging.info("Harvest rasters directory => %s", harvest_cfg.get("raster_directory"))

    final_output_dir = cfg.final_combined_dir()
    logging.info("Final disturbance outputs will be written to => %s", final_output_dir)

    for period in cfg.TIME_PERIODS.keys():
        # Build paths
        fire_path = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period}.tif")
        insect_path = os.path.join(cfg.INSECT_FINAL_DIR, f"insect_damage_{period}.tif")
        harvest_path = cfg.harvest_raster_path(period)

        if not (arcpy.Exists(fire_path) and
                arcpy.Exists(insect_path) and
                arcpy.Exists(harvest_path)):
            logging.warning(f"Missing one or more rasters for period={period}. Skipping.")
            continue

        logging.info(
            "Combining Fire=%s, Insect=%s, Harvest=%s",
            fire_path,
            insect_path,
            harvest_path,
        )

        # Max combination
        fire_ras = Raster(fire_path)
        insect_ras = Raster(insect_path)
        harvest_ras = Raster(harvest_path)

        combined_max = CellStatistics([fire_ras, insect_ras, harvest_ras], "MAXIMUM", "DATA")

        # Example: mask out code=3 => 0 (low severity fire).
        # If you do NOT want to mask out code=3, skip this step.
        final_dist = Con(combined_max == 3, 0, combined_max)

        # Save
        out_raster_path = os.path.join(final_output_dir, f"disturb_{period}.tif")
        final_dist.save(out_raster_path)
        logging.info(f"Final combined disturbance => {out_raster_path}")

    arcpy.CheckInExtension("Spatial")
    logging.info("All time periods processed successfully.")

if __name__ == "__main__":
    main()