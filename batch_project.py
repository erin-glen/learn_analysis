"""
Script: batch_project_rasters.py
Description:
    This script uses ArcPy to batch-project (reproject) all rasters in an
    input folder, matching the spatial reference, cell size, and snap of
    a target raster.

Requirements:
    - ArcGIS Pro or ArcMap installed (so that ArcPy is available).
    - Correct Python environment (with ArcPy) selected in PyCharm.
    - Read & write permissions to input/output folders.
"""

import arcpy
import os


def main():
    # -------------------------------------------------------------------------
    # 1. USER SETTINGS -- Update these paths as needed
    # -------------------------------------------------------------------------
    input_folder = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_v2023-5"
    target_raster = r"C:\GIS\Data\LEARN\SourceData\NEW_NLCD\Annual_NLCD_LndCov_2023_CU_C1V0.tif"
    output_folder = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_v2023-5_project"

    # Optional: Set a resampling method (e.g. "NEAREST", "BILINEAR", "CUBIC").
    # For thematic data (e.g., land cover), "NEAREST" is recommended.
    resampling_method = "NEAREST"

    # -------------------------------------------------------------------------
    # 2. PREP WORK
    # -------------------------------------------------------------------------
    # Set the ArcPy environment workspace
    arcpy.env.workspace = input_folder

    # Read the spatial reference from the target raster
    target_sr = arcpy.Describe(target_raster).spatialReference

    # Get the target raster cell sizes
    desc_target = arcpy.Describe(target_raster)
    cell_size_x = desc_target.meanCellWidth
    cell_size_y = desc_target.meanCellHeight

    # Create a cell size string for x,y
    # (ProjectRaster accepts a string with two values for x- and y-cell sizes)
    cell_size_str = f"{cell_size_x} {cell_size_y}"

    # Set the snap raster to ensure alignment
    arcpy.env.snapRaster = target_raster

    # Ensure output folder exists (create if it doesn't)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # -------------------------------------------------------------------------
    # 3. GET LIST OF RASTERS & LOOP
    # -------------------------------------------------------------------------
    raster_list = arcpy.ListRasters("*", "All")
    if not raster_list:
        print(f"No rasters found in {input_folder}")
        return

    print(f"Found {len(raster_list)} rasters in '{input_folder}'...")

    # Loop over each raster
    for raster_name in raster_list:
        in_raster_path = os.path.join(input_folder, raster_name)

        # Create an output filename (e.g. "basename_projected.tif")
        base_name, ext = os.path.splitext(raster_name)
        out_raster_name = f"{base_name}_projected.tif"
        out_raster_path = os.path.join(output_folder, out_raster_name)

        print(f"Projecting: {in_raster_path} -> {out_raster_path}")

        # ---------------------------------------------------------------------
        # 4. PROJECT RASTER
        # ---------------------------------------------------------------------
        arcpy.management.ProjectRaster(
            in_raster=in_raster_path,
            out_raster=out_raster_path,
            out_coor_system=target_sr,
            resampling_type=resampling_method,
            cell_size=cell_size_str
            # geographic_transform could be specified if needed for datum shifts
        )

    print("All rasters projected successfully!")


# -------------------------------------------------------------------------
# 5. ENTRY POINT
# -------------------------------------------------------------------------
if __name__ == "__main__":
    main()
