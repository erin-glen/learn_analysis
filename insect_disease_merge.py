# insect_disease_merge.py
"""
Merges per-region insect/disease rasters (values {0,5})
into a single CONUS raster for each time period.
"""

import arcpy
import os
import logging

# Import shared config
import disturbance_config as cfg

def main():
    """
    Mosaics all region-level insect/disease rasters (one mosaic per time period),
    skipping if the final mosaic output already exists.
    """
    logging.info("Starting insect_disease_merge...")

    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")

    # Set ArcPy environment
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER

    for period in cfg.TIME_PERIODS.keys():
        # Final mosaic for this time period
        out_name = f"insect_damage_{period}.tif"
        out_path = os.path.join(cfg.INSECT_FINAL_DIR, out_name)

        # Check if the final mosaic already exists
        if arcpy.Exists(out_path):
            logging.info(f"Final mosaic for '{period}' already exists => {out_path}. Skipping merge.")
            continue

        # Collect the region rasters
        region_rasters = []
        for reg in cfg.REGIONS:
            ras_path = os.path.join(cfg.INSECT_OUTPUT_DIR, f"insect_damage_{reg}_{period}.tif")
            if arcpy.Exists(ras_path):
                region_rasters.append(ras_path)
            else:
                logging.warning(f"Missing insect raster for region={reg}, period={period}")

        if not region_rasters:
            logging.warning(f"No insect/disease region rasters found for '{period}', skipping.")
            continue

        logging.info(f"Merging {len(region_rasters)} region rasters => {out_path}")

        # Mosaic method="MAXIMUM" => 5 overrides 0 if there's overlap
        arcpy.management.MosaicToNewRaster(
            input_rasters=region_rasters,
            output_location=cfg.INSECT_FINAL_DIR,
            raster_dataset_name_with_extension=out_name,
            coordinate_system_for_the_raster="",
            pixel_type="8_BIT_UNSIGNED",
            cellsize="",
            number_of_bands="1",
            mosaic_method="MAXIMUM",
            mosaic_colormap_mode="FIRST"
        )

        logging.info(f"Final insect/disease mosaic saved => {out_path}")

    arcpy.CheckInExtension("Spatial")
    logging.info("Insect/disease merge completed successfully.")

if __name__ == "__main__":
    main()
