# config.py
import os
import arcpy

# ─────────────────────────────────────────────────────────────────────────────
# Base directories
# ─────────────────────────────────────────────────────────────────────────────
DATA_FOLDER = r"C:\GIS\Data\LEARN\SourceData"
OUTPUT_BASE_DIR = r"C:\GIS\Data\LEARN\Outputs"

# Valid years for analysis
VALID_YEARS = ["2001", "2004", "2006", "2008", "2011", "2013", "2016", "2019", "2021", "2023"]

# Default cell size
CELL_SIZE = 30

# Subfolders for carbon data
new_carbon = r"Carbon\bigmap_project"
old_carbon = "Carbon"

# ─────────────────────────────────────────────────────────────────────────────
# Default Trees-Outside-Forest (TOF) factors if we find no better data
# ─────────────────────────────────────────────────────────────────────────────
default_config = {
    'emissions_factor': 103,     # Default emissions factor (tC/ha)
    'removals_factor': -3.53,    # Default removals factor (tC/ha/yr)
    'c_to_co2': 44 / 12,
}

# AOI-specific TOF factors (dictionary overrides)
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

# ─────────────────────────────────────────────────────────────────────────────
# Top-level constants for your shapefile paths:
#   - TOF_REMOVALS_SHP:   for state-level removals factor
#   - TOF_EMISSIONS_SHP:  for county-level emissions factor
# ─────────────────────────────────────────────────────────────────────────────
TOF_REMOVALS_SHP = r"C:\GIS\Data\LEARN\SourceData\TOF\state_removal_factors.shp"
TOF_EMISSIONS_SHP = r"C:\GIS\Data\LEARN\SourceData\TOF\az_county_emission_factors.shp"


def get_input_config(year1, year2, aoi_name=None, tree_canopy_source=None):
    """
    Build the input configuration dictionary specifying paths to the relevant
    NLCD rasters, carbon rasters, forest lookups, disturbance rasters, etc.

    If the aoi_name is not in 'aoi_specific_factors', we look up:
      - Removals factor from a state-level shapefile (TOF_REMOVALS_SHP)
      - Emissions factor from a county-level shapefile (TOF_EMISSIONS_SHP)

    For each intersect with the AOI, if multiple polygons overlap, we take
    the simple average of that factor.

    Args:
        year1 (str): Starting year for land cover analysis (must be in VALID_YEARS).
        year2 (str): Ending year for land cover analysis (must be in VALID_YEARS).
        aoi_name (str, optional): Name for the area of interest shapefile (no .shp).
        tree_canopy_source (str, optional): "NLCD", "CBW", "Local", or None.

    Returns:
        dict: Dictionary of all configured paths plus disturbance rasters,
              TOF emission/removal factors, etc.
    """
    # Convert input years to integers
    year1 = int(year1)
    year2 = int(year2)

    # Start by copying our default TOF factors
    config = default_config.copy()

    # Check if AOI matches known ones in aoi_specific_factors
    need_shapefile_lookup = True
    if aoi_name and aoi_name in aoi_specific_factors:
        config.update(aoi_specific_factors[aoi_name])
        need_shapefile_lookup = False
    else:
        print(f"AOI '{aoi_name}' not found in dictionary. Will attempt shapefile lookups for TOF factors.")

    # If not in aoi_specific_factors, try to look up factors from shapefiles
    if need_shapefile_lookup and aoi_name:
        # Path to the user’s AOI shapefile
        aoi_path = os.path.join(DATA_FOLDER, "AOI", f"{aoi_name}.shp")
        if not os.path.exists(aoi_path):
            print(f"AOI shapefile not found: {aoi_path}. Using default TOF factors.")
        else:
            # 1) Lookup REMOVALS in the state-level shapefile
            removals_shp = TOF_REMOVALS_SHP
            if os.path.exists(removals_shp):
                config['removals_factor'] = _get_average_factor_from_shapefile(
                    shp_path=removals_shp,
                    aoi_path=aoi_path,
                    factor_field="tof_rf",
                    default_value=config['removals_factor']
                )
            else:
                print(f"State-level shapefile not found: {removals_shp}. Using default removals factor.")

            # 2) Lookup EMISSIONS in the county-level shapefile
            emissions_shp = TOF_EMISSIONS_SHP
            if os.path.exists(emissions_shp):
                config['emissions_factor'] = _get_average_factor_from_shapefile(
                    shp_path=emissions_shp,
                    aoi_path=aoi_path,
                    factor_field="tof_ef",
                    default_value=config['emissions_factor']
                )
            else:
                print(f"County-level shapefile not found: {emissions_shp}. Using default emissions factor.")

    # ----------------------------------------------------------------------
    # Build the main input_config dict with all raster/file paths
    # ----------------------------------------------------------------------
    input_config = {
        "nlcd_1": os.path.join(
            DATA_FOLDER, "LandCover",
            f"NLCD_{year1}_Land_Cover_l48_20210604.tif"
        ),
        "nlcd_2": os.path.join(
            DATA_FOLDER, "LandCover",
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

    # If AOI name is provided, store the AOI path
    if aoi_name:
        input_config["aoi"] = os.path.join(DATA_FOLDER, "AOI", f"{aoi_name}.shp")

    # Handle tree canopy source logic
    if tree_canopy_source:
        if tree_canopy_source == "NLCD":
            tc_folder = os.path.join(DATA_FOLDER, "TreeCanopy", "NLCD_Project")
            # 2021-2023 logic
            if (year1 == 2021 and year2 == 2023):
                input_config["tree_canopy_1"] = os.path.join(
                    tc_folder, "nlcd_tcc_conus_2019_v2021-4_projected.tif"
                )
                input_config["tree_canopy_2"] = os.path.join(
                    tc_folder, "nlcd_tcc_conus_2021_v2021-4_projected.tif"
                )
            else:
                input_config["tree_canopy_1"] = os.path.join(
                    tc_folder, f"nlcd_tcc_conus_{year1}_v2021-4_projected.tif"
                )
                input_config["tree_canopy_2"] = os.path.join(
                    tc_folder, f"nlcd_tcc_conus_{year2}_v2021-4_projected.tif"
                )

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
        {"name": "disturbance_2123.tif", "start_year": 2021, "end_year": 2023},
    ]

    # Filter the relevant disturbance rasters for the time window
    selected_disturbance_rasters = []
    for dist_info in disturbance_rasters_info:
        start = dist_info["start_year"]
        end = dist_info["end_year"]
        if (start >= year1) and (end <= year2):
            dist_raster_path = os.path.join(DATA_FOLDER, "Disturbances", dist_info["name"])
            selected_disturbance_rasters.append(dist_raster_path)
    input_config["disturbance_rasters"] = selected_disturbance_rasters

    # Plug the final emissions/removals into the config
    input_config['emissions_factor'] = config['emissions_factor']
    input_config['removals_factor'] = config['removals_factor']
    input_config['c_to_co2'] = config['c_to_co2']

    return input_config


def _get_average_factor_from_shapefile(shp_path, aoi_path, factor_field, default_value):
    """
    Intersects the AOI with the given shapefile. If one or more polygons intersect,
    this returns the AVERAGE of the 'factor_field' across all intersecting polygons.

    If no intersects or no valid factor values are found, returns default_value.
    """
    # Make feature layers in memory
    arcpy.management.MakeFeatureLayer(shp_path, "factor_layer")
    arcpy.management.MakeFeatureLayer(aoi_path, "aoi_layer")

    # Select polygons that intersect the AOI
    arcpy.management.SelectLayerByLocation(
        in_layer="factor_layer",
        overlap_type="INTERSECT",
        select_features="aoi_layer",
        selection_type="NEW_SELECTION"
    )

    # Check how many matched
    count_selected = int(arcpy.management.GetCount("factor_layer").getOutput(0))
    if count_selected < 1:
        print(f"No intersecting polygons in {shp_path} for AOI. Using default {factor_field} = {default_value}")
        return default_value

    # Loop through all intersecting features and gather factor values
    factor_values = []
    with arcpy.da.SearchCursor("factor_layer", [factor_field]) as cursor:
        for row in cursor:
            val = row[0]
            if val is not None:
                factor_values.append(val)

    if not factor_values:
        print(f"No valid '{factor_field}' values in {os.path.basename(shp_path)}. Using default = {default_value}")
        return default_value

    # Calculate the simple average
    avg_factor = sum(factor_values) / len(factor_values)
    print(f"Found {len(factor_values)} intersecting polygons in {os.path.basename(shp_path)}; "
          f"using AVERAGE {factor_field} = {avg_factor}")
    return avg_factor
