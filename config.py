# config.py
import os

# Base directories
DATA_FOLDER = r"C:\GIS\Data\LEARN\SourceData"
OUTPUT_BASE_DIR = r"C:\GIS\Data\LEARN\Outputs"

# Valid years for analysis
VALID_YEARS = ["2001", "2004", "2006", "2008", "2011", "2013", "2016", "2019", "2021","2023"]

# Default cell size
CELL_SIZE = 30

new_carbon = r"Carbon\bigmap_project"
old_carbon = "Carbon"

def get_input_config(year1, year2, aoi_name=None, tree_canopy_source=None):
    """
    Build the input configuration dictionary specifying paths to the relevant NLCD rasters,
    carbon rasters, forest lookups, disturbance rasters, etc. Also sets default or AOI-specific
    emissions/removals factors for trees outside forests (TOF).

    Args:
        year1 (str): Starting year for land cover analysis (one of VALID_YEARS).
        year2 (str): Ending year for land cover analysis (one of VALID_YEARS).
        aoi_name (str, optional): Name for the area of interest shapefile (without .shp).
        tree_canopy_source (str, optional): One of {"NLCD", "CBW", "Local"} or None.

    Returns:
        dict: Dictionary of all configured paths (nlcd_1, nlcd_2, forest_age_raster, etc.)
              plus disturbance rasters, TOF emission/removal factors, etc.
    """
    # Convert input year strings to integers for comparisons
    year1 = int(year1)
    year2 = int(year2)

    # Define default emissions and removals factors
    default_config = {
        'emissions_factor': 103,      # Default emissions factor (tC/ha)
        'removals_factor': -3.53,     # Default removals factor (tC/ha/yr)
        'c_to_co2': 44 / 12,
    }

    # Define AOI-specific factors if you have them
    aoi_specific_factors = {
        'Montgomery': {
            'emissions_factor': 103,
            'removals_factor': -3.53,
        },
        'Jefferson': {
            'emissions_factor': 95.9,
            'removals_factor': -2.82,
        },
    }

    # Start from default config
    config = default_config.copy()

    # If AOI name is provided and recognized, override defaults
    if aoi_name and aoi_name in aoi_specific_factors:
        config.update(aoi_specific_factors[aoi_name])
    else:
        # Optionally warn if not recognized
        print(f"Warning: AOI '{aoi_name}' not found in specific factors. Using default values.")

    # Build the main input_config dict
    input_config = {
        "nlcd_1": os.path.join(
            DATA_FOLDER,
            "LandCover",
            f"NLCD_{year1}_Land_Cover_l48_20210604.tif"
        ),
        "nlcd_2": os.path.join(
            DATA_FOLDER,
            "LandCover",
            f"NLCD_{year2}_Land_Cover_l48_20210604.tif"
        ),
        "forest_age_raster": os.path.join(
            DATA_FOLDER, "ForestType", "forest_raster_01062025.tif"
        ),
        "carbon_ag_bg_us": os.path.join(
            DATA_FOLDER, new_carbon, "carbon_ag_bg_us.tif"
        ),
        "carbon_sd_dd_lt": os.path.join(
            DATA_FOLDER, new_carbon, "carbon_sd_dd_lt.tif"
        ),
        "carbon_so": os.path.join(DATA_FOLDER, new_carbon, "carbon_so.tif"),
        "forest_lookup_csv": os.path.join(
            DATA_FOLDER, "ForestType", "forest_raster_09172020.csv"
        ),
        "plantable_areas": "None",
    }

    # If AOI name provided, use it for input_config["aoi"]
    if aoi_name:
        input_config["aoi"] = os.path.join(
            DATA_FOLDER, "AOI", f"{aoi_name}.shp"
        )

    # If tree_canopy_source is set, choose appropriate canopy data
    if tree_canopy_source:
        if tree_canopy_source == "NLCD":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "NLCD_Project")

            # ─────────────────────────────────────────────────────────────────
            # NEW LOGIC: If user picks 2021-2023 for land cover, override
            # canopy to 2019/2021
            # ─────────────────────────────────────────────────────────────────
            if (year1 == 2021 and year2 == 2023):
                input_config["tree_canopy_1"] = os.path.join(
                    tc_folder, "nlcd_tcc_conus_2019_v2021-4_projected.tif"
                )
                input_config["tree_canopy_2"] = os.path.join(
                    tc_folder, "nlcd_tcc_conus_2021_v2021-4_projected.tif"
                )
            else:
                # Normal approach: match canopy to year1/year2
                input_config["tree_canopy_1"] = os.path.join(
                    tc_folder, f"nlcd_tcc_conus_{year1}_v2021-4_projected.tif"
                )
                input_config["tree_canopy_2"] = os.path.join(
                    tc_folder, f"nlcd_tcc_conus_{year2}_v2021-4_projected.tif"
                )
            # ─────────────────────────────────────────────────────────────────

        elif tree_canopy_source == "CBW":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "CBW")
            input_config["tree_canopy_1"] = os.path.join(
                tc_folder, "cbw_2013_treecanopy_Agg30m_int.tif"
            )
            input_config["tree_canopy_2"] = os.path.join(
                tc_folder, "cbw_2018_treecanopy_Agg30m_int.tif"
            )
        elif tree_canopy_source == "Local":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "Local", aoi_name)
            input_config["tree_canopy_1"] = os.path.join(
                tc_folder, f"{aoi_name}_2016.tif"
            )
            input_config["tree_canopy_2"] = os.path.join(
                tc_folder, f"{aoi_name}_2020.tif"
            )
        else:
            raise ValueError(f"Invalid tree canopy source: {tree_canopy_source}")

    # Disturbance rasters info
    disturbance_rasters_info = [
        {"name": "disturbance_0104.tif", "start_year": 2001, "end_year": 2004},
        {"name": "disturbance_0406.tif", "start_year": 2004, "end_year": 2006},
        {"name": "disturbance_0608.tif", "start_year": 2006, "end_year": 2008},
        {"name": "disturbance_0811.tif", "start_year": 2008, "end_year": 2011},
        {"name": "disturbance_1113.tif", "start_year": 2011, "end_year": 2013},
        {"name": "disturbance_1316.tif", "start_year": 2013, "end_year": 2016},
        {"name": "disturbance_1619.tif", "start_year": 2016, "end_year": 2019},
        {"name": "disturbance_1921.tif", "start_year": 2019, "end_year": 2021},
        {"name": "disturbance_2131.tif", "start_year": 2021, "end_year": 2023},
    ]

    # Pick disturbance rasters fully inside the analysis period
    selected_disturbance_rasters = []
    for dist_info in disturbance_rasters_info:
        start = dist_info["start_year"]
        end = dist_info["end_year"]
        # If the disturbance window is entirely within the user’s chosen [year1, year2]
        if (start >= year1) and (end <= year2):
            dist_raster_path = os.path.join(DATA_FOLDER, "Disturbances", dist_info["name"])
            selected_disturbance_rasters.append(dist_raster_path)

    input_config["disturbance_rasters"] = selected_disturbance_rasters

    # Emissions/Removals
    input_config['emissions_factor'] = config['emissions_factor']
    input_config['removals_factor'] = config['removals_factor']
    input_config['c_to_co2'] = config['c_to_co2']

    return input_config
