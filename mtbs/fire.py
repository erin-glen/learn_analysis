import arcpy
from arcpy.sa import *
import os

# Checkout Spatial Analyst
arcpy.CheckOutExtension("Spatial")

# -------------------------------------------------------------------
# 1. CONFIGURATIONS
# -------------------------------------------------------------------
# Root directory containing annual MTBS rasters:
#   e.g. "C:\GIS\Data\LEARN\Disturbances\Fire\Raw\composite_data\MTBS_BSmosaics"
fire_root = r"C:\GIS\Data\LEARN\Disturbances\Fire\Raw\composite_data\MTBS_BSmosaics"

# Output directory for final reclassified raster
output_dir = r"C:\GIS\Data\LEARN\Disturbances\Fire\Processed"
os.makedirs(output_dir, exist_ok=True)

# Path to the NLCD 2021 raster (snap/extent reference)
nlcd_raster = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"

# ArcPy environment
arcpy.env.snapRaster = nlcd_raster
arcpy.env.extent = nlcd_raster
arcpy.env.cellSize = nlcd_raster
arcpy.env.outputCoordinateSystem = nlcd_raster
arcpy.env.overwriteOutput = True

# Inventory years to consider
years = [2021, 2022, 2023]

# The final multi-year output name
out_final_raster = os.path.join(output_dir, "fire_2021_2023.tif")

# -------------------------------------------------------------------
# 2. BUILD PATHS & COLLECT RAW MTBS RASTERS
# -------------------------------------------------------------------
ras_list = []
for yr in years:
    # For each year: path: fire_root\{year}\mtbs_CONUS_{year}\mtbs_CONUS_{year}.tif
    in_tif = os.path.join(fire_root, str(yr), f"mtbs_CONUS_{yr}", f"mtbs_CONUS_{yr}.tif")
    if arcpy.Exists(in_tif):
        print(f"Found MTBS for {yr}: {in_tif}")
        ras_list.append(Raster(in_tif))
    else:
        print(f"Warning: {in_tif} does NOT exist; skipping year={yr}")

if not ras_list:
    print("No raw rasters found for the specified years. Exiting.")
    exit()

# -------------------------------------------------------------------
# 3. CELLSTATISTICS (MAX) ON RAW MTBS
# -------------------------------------------------------------------
# Each raw code is 1..6 or NoData. We want the maximum code across years.
print(f"Combining {len(ras_list)} raw rasters via MAX => {out_final_raster} (temp)")

max_raw = CellStatistics(ras_list, "MAXIMUM", "DATA")  # ignore NoData => won't overshadow valid pixels

# -------------------------------------------------------------------
# 4. RECLASSIFY THE COMBINED RASTER: 1,2,5=>3; 3,4=>10; 6 or NoData=>0
# -------------------------------------------------------------------
# We'll define a RemapValue with old->new codes
reclass_map = RemapValue([
    [1, 3],  # Unburned to low => 3
    [2, 3],  # Low => 3
    [3, 10], # Moderate => 10
    [4, 10], # High => 10
    [5, 3],  # Increased Greenness => 3
    [6, 0],  # Unprocessed => 0
    # NoData => 0 via an extra step with Con(IsNull(...),0,...) or "NODATA" param below
])

# ArcPy Reclassify
rc = Reclassify(max_raw, "Value", reclass_map, "NODATA")  # all other codes => NoData
final_fire = Con(IsNull(rc), 0, rc)  # if NoData => 0, else => the reclassed code

# Save the final
print(f"Reclassifying final raw => {out_final_raster}")
final_fire.save(out_final_raster)

print("Fire inventory reclassification (2021–2023) completed.")
