# config.py

import os

# Base directories
DATA_FOLDER = r"C:\GIS\Data\LEARN\SourceData"
OUTPUT_BASE_DIR = r"C:\GIS\Data\LEARN\Outputs"

# Valid years for analysis
VALID_YEARS = ["2001", "2004", "2006", "2008", "2011", "2013", "2016", "2019", "2021"]

# Default cell size
CELL_SIZE = 30

# Function to construct input paths
def get_input_config(year1, year2, aoi_name=None, tree_canopy_source=None):
    # Build the input configuration dictionary
    input_config = {
        "nlcd_1": os.path.join(DATA_FOLDER, "LandCover", f"NLCD_{year1}_Land_Cover_l48_20210604.tif"),
        "nlcd_2": os.path.join(DATA_FOLDER, "LandCover", f"NLCD_{year2}_Land_Cover_l48_20210604.tif"),
        "forest_age_raster": os.path.join(DATA_FOLDER, "ForestType", "forest_raster_07232020.tif"),
        "carbon_ag_bg_us": os.path.join(DATA_FOLDER, "Carbon", "carbon_ag_bg_us.tif"),
        "carbon_sd_dd_lt": os.path.join(DATA_FOLDER, "Carbon", "carbon_sd_dd_lt.tif"),
        "carbon_so": os.path.join(DATA_FOLDER, "Carbon", "carbon_so.tif"),
        "forest_lookup_csv": os.path.join(DATA_FOLDER, "ForestType", "forest_raster_09172020.csv"),
        "plantable_areas": "None",
        "disturbance_rasters": [
            os.path.join(DATA_FOLDER, "Disturbances", "disturbance_1113.tif"),
            os.path.join(DATA_FOLDER, "Disturbances", "disturbance_1316.tif"),
            os.path.join(DATA_FOLDER, "Disturbances", "disturbance_1619.tif"),
        ],
    }

    # Add AOI if provided
    if aoi_name:
        input_config["aoi"] = os.path.join(DATA_FOLDER, "AOI", f"{aoi_name}.shp")

    # Add tree canopy paths if provided
    if tree_canopy_source:
        if tree_canopy_source == "NLCD":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "NLCD")
            input_config["tree_canopy_1"] = os.path.join(tc_folder, f"nlcd_tcc_conus_{year1}_v2021-4.tif")
            input_config["tree_canopy_2"] = os.path.join(tc_folder, f"nlcd_tcc_conus_{year2}_v2021-4.tif")
        elif tree_canopy_source == "CBW":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "CBW")
            input_config["tree_canopy_1"] = os.path.join(tc_folder, "cbw_2013_treecanopy_Agg30m_int.tif")
            input_config["tree_canopy_2"] = os.path.join(tc_folder, "cbw_2018_treecanopy_Agg30m_int.tif")
        elif tree_canopy_source == "Local":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "Local", aoi_name)
            input_config["tree_canopy_1"] = os.path.join(tc_folder, f"{aoi_name}_2016.tif")
            input_config["tree_canopy_2"] = os.path.join(tc_folder, f"{aoi_name}_2020.tif")
        else:
            raise ValueError(f"Invalid tree canopy source: {tree_canopy_source}")

    return input_config
