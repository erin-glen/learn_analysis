import arcpy
from arcpy.sa import *
import os

# Checkout Spatial Analyst
arcpy.CheckOutExtension("Spatial")

# ----------------------------------------------------------------------
# 1. CONFIGURATIONS
# ----------------------------------------------------------------------
# Folder containing your intermediate per-time-period rasters:
#   fire_{period}.tif, insect_damage_{period}.tif, hansen_{period}.tif
input_folder = r"C:\GIS\Data\LEARN\Disturbances\Intermediate"

# Output folder for the final combined disturbance rasters
final_folder = r"C:\GIS\Data\Disturbances\FinalCombined"
os.makedirs(final_folder, exist_ok=True)

# NLCD reference raster for environment settings
nlcd_raster = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"
arcpy.env.snapRaster = nlcd_raster
arcpy.env.extent = nlcd_raster
arcpy.env.cellSize = nlcd_raster
arcpy.env.outputCoordinateSystem = nlcd_raster
arcpy.env.overwriteOutput = True

# Define which time periods to process
time_periods = [
    "2021_2023"
]

# ----------------------------------------------------------------------
# 2. AUTOMATED FINAL COMBINATION
# ----------------------------------------------------------------------
# For each time period:
#   1) Check for fire_{period}, insect_damage_{period}, hansen_{period}
#   2) CellStatistics("MAXIMUM","DATA") => combine the three
#   3) Con( combined==3, 0, combined ) => mask out low-severity fire (3->0)
#   4) Save as disturb_{period}.tif

for period in time_periods:
    # Construct expected input rasters
    fire_path = os.path.join(input_folder, f"fire_{period}.tif")
    insect_path = os.path.join(input_folder, f"insect_damage_{period}.tif")
    hansen_path = os.path.join(input_folder, f"hansen_{period}.tif")

    # Check if all three exist
    if not arcpy.Exists(fire_path):
        print(f"Warning: {fire_path} not found. Skipping {period}.")
        continue
    if not arcpy.Exists(insect_path):
        print(f"Warning: {insect_path} not found. Skipping {period}.")
        continue
    if not arcpy.Exists(hansen_path):
        print(f"Warning: {hansen_path} not found. Skipping {period}.")
        continue

    print(f"\nCombining Fire, Insect, Harvest for {period} => disturb_{period}.tif")

    # 1) Load each as a Raster object
    fire_ras = Raster(fire_path)
    insect_ras = Raster(insect_path)
    hansen_ras = Raster(hansen_path)

    # 2) Use CellStatistics to pick the maximum code among fire,insect,hansen
    combined_max = CellStatistics([fire_ras, insect_ras, hansen_ras],
                                  "MAXIMUM", "DATA")

    # 3) Mask out code=3 => 0, everything else stays the same
    #   Con( combined_max==3, 0, combined_max )
    final_dist = Con(combined_max == 3, 0, combined_max)

    # 4) Save final output
    out_raster_path = os.path.join(final_folder, f"disturb_{period}.tif")
    final_dist.save(out_raster_path)

    print(f"Final combined raster saved: {out_raster_path}")

# Cleanup
arcpy.CheckInExtension("Spatial")
print("\nAll time periods processed successfully.")
