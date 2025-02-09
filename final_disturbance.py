import arcpy
from arcpy.sa import *
import os

# Checkout Spatial Analyst extension
arcpy.CheckOutExtension("Spatial")

# ----------------------------------------------------------------------
# 1. CONFIGURATIONS
# ----------------------------------------------------------------------
# Where your processed rasters for each dataset + time period are stored
# e.g., you have something like:
#   fire_2006_2008.tif
#   insect_2006_2008.tif
#   hansen_2006_2008.tif
# in this folder:
input_folder = r"C:\GIS\Data\LEARN\Disturbances\Intermediate"

# Output folder for final combined rasters
final_folder = r"C:\GIS\Data\Disturbances\FinalCombined"
os.makedirs(final_folder, exist_ok=True)

# NLCD reference raster (for environment settings)
nlcd_raster = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"

arcpy.env.snapRaster = nlcd_raster
arcpy.env.extent = nlcd_raster
arcpy.env.cellSize = nlcd_raster
arcpy.env.outputCoordinateSystem = nlcd_raster
arcpy.env.overwriteOutput = True

# Example NLCD time periods
time_periods = [
    "2021_2023"
]

# ----------------------------------------------------------------------
# 2. AUTOMATED FINAL COMBINATION
# ----------------------------------------------------------------------
# For each time period:
#   1) Locate the three rasters: fire_{period}, insect_{period}, hansen_{period}
#   2) CellStatistics("MAX") => combined_max
#   3) Reclassify "3 => 0" to mask out low-severity fires
#   4) Save final as disturb_{period}.tif

for period in time_periods:
    fire_ras = os.path.join(input_folder, f"fire_{period}.tif")
    insect_ras = os.path.join(input_folder, f"insect_damage_{period}.tif")
    hansen_ras = os.path.join(input_folder, f"hansen_{period}.tif")

    # Check that all exist
    if not arcpy.Exists(fire_ras):
        print(f"Warning: {fire_ras} does not exist. Skipping {period}.")
        continue
    if not arcpy.Exists(insect_ras):
        print(f"Warning: {insect_ras} does not exist. Skipping {period}.")
        continue
    if not arcpy.Exists(hansen_ras):
        print(f"Warning: {hansen_ras} does not exist. Skipping {period}.")
        continue

    print(f"\nCombining Fire, Insect, Harvest for {period} => disturb_{period}.tif")

    # 1) Load them as Raster objects
    fire_raster = Raster(fire_ras)
    insect_raster = Raster(insect_ras)
    hansen_raster = Raster(hansen_ras)

    # 2) CellStatistics("MAX", "DATA")
    combined_max = CellStatistics([fire_raster, insect_raster, hansen_raster],
                                  "MAX", "DATA")

    # 3) Reclassify "3 => 0" to mask out low-severity fires
    # Use a RemapValue with only one entry: [3,0],
    #  missing_values="NODATA" => anything else remains unchanged
    reclass_map = RemapValue([[3, 0]])
    final_dist = Reclassify(in_raster=combined_max,
                            reclass_field="Value",
                            remap=reclass_map,
                            missing_values="NODATA")

    # 4) Save final output
    out_raster_path = os.path.join(final_folder, f"disturb_{period}.tif")
    final_dist.save(out_raster_path)

    print(f"Final combined raster saved: {out_raster_path}")

# Cleanup
arcpy.CheckInExtension("Spatial")

print("\nAll time periods processed successfully.")
