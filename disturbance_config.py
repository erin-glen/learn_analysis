# disturbance_config.py

import os
import logging

# --------------------------------------------------------------------
# LOGGING CONFIG
# --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# --------------------------------------------------------------------
# GENERAL PATHS
# --------------------------------------------------------------------
# You can modify these paths to suit your directory structure.
BASE_DIR = r"C:\GIS\Data\LEARN\Disturbances"

# For Insect/Disease:
INSECT_GDB_DIR = os.path.join(BASE_DIR, "ADS")
INSECT_OUTPUT_DIR = os.path.join(INSECT_GDB_DIR, "Processed")

# For final insect rasters:
INSECT_FINAL_DIR = os.path.join(INSECT_GDB_DIR, "Final")

# For Harvest/Other (Hansen):
HANSEN_INPUT_DIR = os.path.join(BASE_DIR, "Hansen")
HANSEN_OUTPUT_DIR = os.path.join(HANSEN_INPUT_DIR, "Processed")

# For Harvest/Other (NLCD Tree Canopy Change):
NLCD_TCC_INPUT_DIR = os.path.join(BASE_DIR, "NLCD_TreeCanopy")
NLCD_TCC_OUTPUT_DIR = os.path.join(NLCD_TCC_INPUT_DIR, "Processed")

# NLCD Tree Canopy rasters by year (update the filenames to match your data)
NLCD_TCC_RASTERS = {
    2016: os.path.join(NLCD_TCC_INPUT_DIR, "nlcd_tree_canopy_2016.tif"),
    2019: os.path.join(NLCD_TCC_INPUT_DIR, "nlcd_tree_canopy_2019.tif"),
    2021: os.path.join(NLCD_TCC_INPUT_DIR, "nlcd_tree_canopy_2021.tif"),
    2023: os.path.join(NLCD_TCC_INPUT_DIR, "nlcd_tree_canopy_2023.tif"),
}

# Thresholds (upper bounds) for assigning canopy-loss severity classes 1-4
NLCD_TCC_SEVERITY_BREAKS = [25, 50, 75, 100]

# For Fire:
FIRE_ROOT = os.path.join(BASE_DIR, "Fire", "Raw", "composite_data", "MTBS_BSmosaics")
FIRE_OUTPUT_DIR = os.path.join(BASE_DIR, "Fire", "Processed")

# For final combination steps:
INTERMEDIATE_COMBINED_DIR = os.path.join(BASE_DIR, "Intermediate")
FINAL_COMBINED_DIR = os.path.join(BASE_DIR, "FinalCombined")

# Create directories if they don’t exist:
for _dir in [
    INSECT_OUTPUT_DIR,
    INSECT_FINAL_DIR,
    HANSEN_OUTPUT_DIR,
    NLCD_TCC_OUTPUT_DIR,
    FIRE_OUTPUT_DIR,
    INTERMEDIATE_COMBINED_DIR,
    FINAL_COMBINED_DIR
]:
    os.makedirs(_dir, exist_ok=True)

# --------------------------------------------------------------------
# NLCD / REFERENCE RASTER
# --------------------------------------------------------------------
NLCD_RASTER = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"

# --------------------------------------------------------------------
# REGIONS & TIME PERIODS
# --------------------------------------------------------------------
REGIONS = [1, 2, 3, 4, 6, 8, 9]

# Example time periods, as a dict of "name" -> [year1, year2, year3, ...]
TIME_PERIODS = {
    "2021_2023": [2021, 2022, 2023],
}

# --------------------------------------------------------------------
# HANSEN TILES
# --------------------------------------------------------------------
HANSEN_TILES = [
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_130W.tif",
]