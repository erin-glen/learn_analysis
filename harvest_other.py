# harvest_other.py
"""
Processes Hansen 'harvest/other' data by:
1) (Optional) Mosaicking multiple tiles (if not already done)
2) (Optional) Reprojecting the mosaic to match NLCD (if not already done)
3) Extracting disturbance in the specified time periods

This is the legacy harvest workflow invoked when
``disturbance_config.HARVEST_WORKFLOW == "hansen"``.

Example
-------
Run against a pair of tiles (identified by the Hansen filename stem):

```
python harvest_other.py --tile-id GFW2023_40N_090W --tile-id 40N_100W
```

Omit ``--tile-id`` to process all configured tiles.
"""

import arcpy
import os
import logging
import sys
import argparse
from typing import Iterable, Sequence

import disturbance_config as cfg


def _parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process Hansen harvest/other rasters, optionally filtered to specific tiles.",
    )
    parser.add_argument(
        "--tile-id",
        dest="tile_ids",
        action="append",
        default=[],
        metavar="TILE_ID",
        help=(
            "Limit processing to a Hansen tile id. Repeat for multiple ids. "
            "The value may be the full stem (e.g. GFW2023_40N_090W) or the core "
            "row/column portion (e.g. 40N_090W)."
        ),
    )
    parser.add_argument(
        "--tiles",
        dest="tile_csv",
        metavar="ID1,ID2",
        help="Comma-separated list of Hansen tile ids to include.",
    )
    return parser.parse_args(argv)


def _combine_tile_args(tile_ids: Iterable[str], csv: str | None) -> list[str]:
    combined = list(tile_ids) if tile_ids else []
    if csv:
        combined.extend(part.strip() for part in csv.split(",") if part.strip())
    return combined


def main(tile_ids: Iterable[str] | None = None):
    """
    Creates a single Hansen mosaic, reprojects it to match NLCD,
    then for each time period, extracts disturbance (1) vs no-disturbance (0).
    Steps 1 & 2 are only done if the relevant outputs do not already exist,
    and each period's extraction step is skipped if that output already exists.
    """
    logging.info("Starting harvest_other.py (Hansen-based harvest/other).")

    # ArcPy environment setup
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER

    # Resolve tile selection (if any)
    tile_ids = list(tile_ids) if tile_ids else []
    selected_tiles = cfg.hansen_tile_paths(tile_ids)

    # Output paths
    hansen_mosaic_raw = os.path.join(cfg.HANSEN_OUTPUT_DIR, "hansen_mosaic_raw.tif")
    hansen_mosaic_reproj = os.path.join(cfg.HANSEN_OUTPUT_DIR, "hansen_mosaic_reproj.tif")

    # 1) Mosaic (only if needed)
    if not arcpy.Exists(hansen_mosaic_raw):
        logging.info("Mosaicking Hansen tiles into a single raw raster:")
        for t in selected_tiles:
            logging.info(f"  {t}")

        logging.info(f"Creating mosaic => {hansen_mosaic_raw}")
        arcpy.management.MosaicToNewRaster(
            input_rasters=selected_tiles,
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
        if tile_ids:
            logging.info(
                "Tile filter requested (%s) but existing mosaic will be reused. Delete the mosaic "
                "if you need to rebuild it from only the specified tiles.",
                ", ".join(tile_ids),
            )

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
        if tile_ids:
            logging.info(
                "Tile filter requested (%s) but existing reprojected mosaic will be reused. Delete the "
                "reprojected raster if you need a tile-specific product.",
                ", ".join(tile_ids),
            )

    # 3) Extract time periods from the reprojected mosaic
    from arcpy.sa import Raster, Con
    logging.info("Extracting inventory periods from Hansen reprojected mosaic...")

    h = Raster(hansen_mosaic_reproj)
    for period_name, years in cfg.TIME_PERIODS.items():
        out_raster = os.path.join(cfg.HANSEN_OUTPUT_DIR, f"hansen_{period_name}.tif")

        # Check if output for this period already exists
        if arcpy.Exists(out_raster):
            logging.info(f"Harvest raster for '{period_name}' already exists => {out_raster}. Skipping extraction.")
            continue

        logging.info(f"  -> Creating harvest raster for '{period_name}' => {out_raster}")

        # If years = [2021,2022,2023], Hansen codes => (year - 2001)+1
        low_val = (min(years) - 2001) + 1
        high_val = (max(years) - 2001) + 1

        # Con(...) => 1 if pixel code in [low_val..high_val], else 0
        cond = (h >= low_val) & (h <= high_val)
        out_period = Con(cond, 1, 0)
        out_period.save(out_raster)

        logging.info(f"Harvest raster saved => {out_raster}")

    # Cleanup
    arcpy.CheckInExtension("Spatial")
    logging.info("Hansen 'harvest/other' processing completed successfully.")

if __name__ == "__main__":
    args = _parse_cli_args()
    combined_tiles = _combine_tile_args(args.tile_ids, args.tile_csv)
    main(tile_ids=combined_tiles)
