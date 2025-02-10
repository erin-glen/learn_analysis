# fire.py
"""
Processes fire data from MTBS annual composites:
1) Finds relevant yearly rasters
2) Performs a CellStatistics MAX across those years
3) Reclassifies final codes for each pixel
"""

import arcpy
from arcpy.sa import *
import os
import logging

import disturbance_config as cfg

def main():
    """
    Builds a multi-year fire disturbance raster, reclassifying
    severity codes to simplified values.
    """
    logging.info("Starting fire.py...")

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER
    arcpy.env.overwriteOutput = True

    # For demonstration, assume all time_periods share same final name "fire_{period}.tif"
    for period_name, years in cfg.TIME_PERIODS.items():
        out_final_raster = os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{period_name}.tif")

        # Build path list
        ras_list = []
        for yr in years:
            in_tif = os.path.join(cfg.FIRE_ROOT, str(yr), f"mtbs_CONUS_{yr}", f"mtbs_CONUS_{yr}.tif")
            if arcpy.Exists(in_tif):
                logging.info(f"Found MTBS for year={yr}: {in_tif}")
                ras_list.append(Raster(in_tif))
            else:
                logging.warning(f"MTBS year={yr} not found: {in_tif}")

        if not ras_list:
            logging.warning(f"No raw fire rasters found for period={period_name}. Skipping.")
            continue

        logging.info(f"Combining {len(ras_list)} raw rasters via MAX => {out_final_raster}")

        max_raw = CellStatistics(ras_list, "MAXIMUM", "DATA")  # ignore NoData

        # Reclassify codes: 1,2,5 => 3; 3,4 => 10; 6 or NoData => 0
        reclass_map = RemapValue([
            [1, 3],
            [2, 3],
            [3, 10],
            [4, 10],
            [5, 3],
            [6, 0]
        ])
        rc = Reclassify(max_raw, "Value", reclass_map, "NODATA")
        final_fire = Con(IsNull(rc), 0, rc)

        final_fire.save(out_final_raster)
        logging.info(f"Fire inventory reclassification => {out_final_raster}")

    arcpy.CheckInExtension("Spatial")
    logging.info("Fire processing completed successfully.")

if __name__ == "__main__":
    main()
