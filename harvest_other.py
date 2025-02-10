# harvest_other.py
"""
Processes Hansen 'harvest/other' data by:
1) (Optional) Mosaicking multiple tiles (if not already done)
2) (Optional) Reprojecting the mosaic to match NLCD (if not already done)
3) Extracting disturbance in the specified time periods
"""

import arcpy
import os
import logging
import sys

import disturbance_config as cfg

def main():
    """
    Creates a single Hansen mosaic, reprojects it to match NLCD,
    then for each time period, extracts disturbance (1) vs no-disturbance (0).
    Steps 1 & 2 are only done if the relevant outputs do not already exist.
    """
    logging.info("Starting harvest_other.py (Hansen-based harvest/other).")

    # ArcPy environment setup
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER

    # Output paths
    hansen_mosaic_raw = os.path.join(cfg.HANSEN_OUTPUT_DIR, "hansen_mosaic_raw.tif")
    hansen_mosaic_reproj = os.path.join(cfg.HANSEN_OUTPUT_DIR, "hansen_mosaic_reproj.tif")

    # 1) Mosaic (only if needed)
    if not arcpy.Exists(hansen_mosaic_raw):
        logging.info("Mosaicking Hansen tiles into a single raw raster:")
        for t in cfg.HANSEN_TILES:
            logging.info(f"  {t}")

        logging.info(f"Creating mosaic => {hansen_mosaic_raw}")
        arcpy.management.MosaicToNewRaster(
            input_rasters=cfg.HANSEN_TILES,
            output_location=os.path.dirname(hansen_mosaic_raw),
            raster_dataset_name_with_extension=os.path.basename(hansen_mosaic_raw),
            coordinate_system_for_the_raster="",
            pixel_type="8_BIT_UNSIGNED",
            cellsize="",
            number_of_bands="1",
            mosaic_method="LAST",
            mosaic_colormap_mode="FIRST"
        )
    else:
        logging.info(f"Mosaic already exists => {hansen_mosaic_raw}. Skipping mosaic step.")

    # 2) Reproject (only if needed)
    if not arcpy.Exists(hansen_mosaic_reproj):
        logging.info(f"Reprojecting => {hansen_mosaic_reproj}")
        arcpy.management.ProjectRaster(
            in_raster=hansen_mosaic_raw,
            out_raster=hansen_mosaic_reproj,
            out_coor_system=cfg.NLCD_RASTER,
            resampling_type="NEAREST",
            cell_size=arcpy.Describe(cfg.NLCD_RASTER).meanCellWidth
        )
    else:
        logging.info(f"Reprojected mosaic already exists => {hansen_mosaic_reproj}. Skipping reproject step.")

    # 3) Extract time periods from the reprojected mosaic
    from arcpy.sa import Raster, Con
    logging.info("Extracting inventory periods from Hansen reprojected mosaic...")

    h = Raster(hansen_mosaic_reproj)
    for period_name, years in cfg.TIME_PERIODS.items():
        out_raster = os.path.join(cfg.HANSEN_OUTPUT_DIR, f"hansen_{period_name}.tif")
        if arcpy.Exists(out_raster):
            logging.info(f"Harvest raster for {period_name} already exists => {out_raster}. Skipping extraction.")
            continue

        logging.info(f"  -> {period_name} => {out_raster}")

        # If years = [2021,2022,2023], Hansen codes => (year - 2001)+1
        low_val = (min(years) - 2001) + 1
        high_val = (max(years) - 2001) + 1

        # Con(...) => 1 if pixel code in [low_val..high_val], else 0
        cond = (h >= low_val) & (h <= high_val)
        out_period = Con(cond, 1, 0)
        out_period.save(out_raster)

    # Cleanup
    arcpy.CheckInExtension("Spatial")
    logging.info("Hansen 'harvest/other' processing completed successfully.")

if __name__ == "__main__":
    main()
