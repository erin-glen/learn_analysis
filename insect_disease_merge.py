# insect_disease_merge.py
"""
Merges per-region insect/disease rasters (values {0,5,6})
into a single CONUS raster for each time period.

Example
-------
```
python insect_disease_merge.py
python insect_disease_merge.py --input-date-subdir 20260304
```
"""

import argparse
import arcpy
import os
import logging
from datetime import datetime

# Import shared config
import disturbance_config as cfg


def _resolve_input_dir(input_dir: str | None = None, input_date_subdir: str | None = None) -> str:
    """Resolve where per-region insect rasters should be read from."""
    if input_dir:
        return input_dir

    if input_date_subdir:
        return os.path.join(cfg.INSECT_OUTPUT_ROOT_DIR, input_date_subdir)

    return cfg.INSECT_OUTPUT_DIR


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge per-region insect rasters into period-level CONUS rasters. "
            "By default, uses cfg.INSECT_OUTPUT_DIR (the current configured date subdir)."
        )
    )
    parser.add_argument(
        "--input-dir",
        help="Optional explicit directory containing insect_damage_{region}_{period}.tif inputs.",
    )
    parser.add_argument(
        "--input-date-subdir",
        help=(
            "Optional date subfolder name under cfg.INSECT_OUTPUT_ROOT_DIR to read inputs from "
            "(for example: 20260304)."
        ),
    )
    return parser.parse_args()


def main(input_dir: str | None = None, input_date_subdir: str | None = None):
    """
    Mosaics all region-level insect/disease rasters (one mosaic per time period),
    skipping if the final mosaic output already exists.
    """
    logging.info("Starting insect_disease_merge...")

    source_dir = _resolve_input_dir(input_dir=input_dir, input_date_subdir=input_date_subdir)
    if not os.path.isdir(source_dir):
        logging.warning("Input directory does not exist: %s", source_dir)

    cfg.write_run_metadata(
        [cfg.INSECT_FINAL_DIR],
        script_name="insect_disease_merge.py",
        parameters={
            "source_dir": source_dir,
            "source_date_subdir": input_date_subdir or "default",
            "run_date": datetime.now().strftime("%Y%m%d"),
        },
    )

    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")

    # Set ArcPy environment
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER

    periods = getattr(cfg, "INSECT_TIME_PERIODS", cfg.TIME_PERIODS_ALL).keys()
    for period in periods:
        out_name = f"insect_damage_{period}.tif"
        out_path = os.path.join(cfg.INSECT_FINAL_DIR, out_name)

        if arcpy.Exists(out_path):
            logging.info("Final mosaic for '%s' already exists => %s. Skipping merge.", period, out_path)
            continue

        region_rasters = []
        for reg in cfg.REGIONS:
            ras_path = os.path.join(source_dir, f"insect_damage_{reg}_{period}.tif")
            if arcpy.Exists(ras_path):
                region_rasters.append(ras_path)
            else:
                logging.warning("Missing insect raster for region=%s, period=%s, path=%s", reg, period, ras_path)

        if not region_rasters:
            logging.warning("No insect/disease region rasters found for '%s' in %s, skipping.", period, source_dir)
            continue

        logging.info("Merging %s region rasters => %s", len(region_rasters), out_path)

        arcpy.management.MosaicToNewRaster(
            input_rasters=region_rasters,
            output_location=cfg.INSECT_FINAL_DIR,
            raster_dataset_name_with_extension=out_name,
            coordinate_system_for_the_raster="",
            pixel_type="8_BIT_UNSIGNED",
            cellsize="",
            number_of_bands="1",
            mosaic_method="MAXIMUM",
            mosaic_colormap_mode="FIRST",
        )

        logging.info("Final insect/disease mosaic saved => %s", out_path)

    arcpy.CheckInExtension("Spatial")
    logging.info("Insect/disease merge completed successfully.")


if __name__ == "__main__":
    args = _parse_cli_args()
    main(input_dir=args.input_dir, input_date_subdir=args.input_date_subdir)
