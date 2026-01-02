#!/usr/bin/env python3
"""
Reproject NLCD Tree Canopy Cover rasters to match the NLCD reference grid.

- Scans the primary and fallback Tree Canopy directories from disturbance_config.
- Finds rasters that match the projected naming convention *without* the
  "_projected" suffix (i.e., inputs).
- Writes "_projected.tif" outputs alongside the inputs.
"""

import glob
import logging
import os
from typing import Iterable, List

import arcpy

import disturbance_config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _candidate_dirs() -> List[str]:
    dirs = [cfg.NLCD_TCC_INPUT_DIR, cfg.NLCD_TCC_INPUT_DIR_FALLBACK]
    return [d for d in dirs if d and os.path.isdir(d)]


def _iter_unprojected_rasters(search_dirs: Iterable[str]) -> Iterable[str]:
    for base in search_dirs:
        for path in glob.glob(os.path.join(base, "*.tif")):
            if path.lower().endswith("_projected.tif"):
                continue
            yield path


def _projected_path(src_path: str) -> str:
    root, ext = os.path.splitext(src_path)
    return f"{root}_projected{ext}"


def _set_env(ref_path: str) -> None:
    d = arcpy.Describe(ref_path)
    arcpy.env.snapRaster = ref_path
    arcpy.env.cellSize = ref_path
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference


def main() -> None:
    if not os.path.exists(cfg.NLCD_RASTER):
        raise FileNotFoundError(f"Reference raster not found: {cfg.NLCD_RASTER}")

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True

    _set_env(cfg.NLCD_RASTER)

    input_dirs = _candidate_dirs()
    if not input_dirs:
        logging.warning("No tree canopy input directories found.")
        return

    rasters = list(_iter_unprojected_rasters(input_dirs))
    if not rasters:
        logging.info("No unprojected tree canopy rasters found.")
        return

    logging.info("Found %d unprojected rasters.", len(rasters))
    for src_path in rasters:
        out_path = _projected_path(src_path)
        if arcpy.Exists(out_path) or os.path.exists(out_path):
            logging.info("Skipping existing output: %s", out_path)
            continue

        logging.info("Projecting %s -> %s", src_path, out_path)
        arcpy.management.ProjectRaster(
            src_path,
            out_path,
            arcpy.env.outputCoordinateSystem,
            resampling_type="NEAREST",
            cell_size=arcpy.env.cellSize,
        )

    logging.info("Completed tree canopy reprojection.")


if __name__ == "__main__":
    main()
