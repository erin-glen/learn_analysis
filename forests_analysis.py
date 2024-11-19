# offline_analysis.py

"""
Batch processing script for land use change analysis.

This script processes multiple geographies (AOIs) to analyze land use changes,
carbon stock variations, and greenhouse gas emissions/removals over time.

Author: [Your Name]
Date: [Current Date]
"""

import os
from datetime import datetime
import arcpy
import pandas as pd
from arcpy.sa import Con, Raster
from lookups import (
    nlcdParentRollupCategories,
    nlcdCategories,
    disturbanceLookup,
    carbonStockLoss,
)

from funcs import (
    tabulate_area_by_stratification,
    determine_landuse_category,
    calculate_forest_removals_and_emissions,
    fill_na_values,
    compute_disturbance_max,
    zonal_sum_carbon,
    create_landuse_stratification_raster,
    calculate_disturbances,
    merge_age_factors,
    calculate_forest_to_nonforest_emissions,
    summarize_ghg,
)

# Ensure overwriting of outputs
arcpy.env.overwriteOutput = True

# Suppress pandas chained assignment warnings
pd.options.mode.chained_assignment = None

# Check out the Spatial Analyst extension
if arcpy.CheckExtension("Spatial") == "Available":
    arcpy.CheckOutExtension("Spatial")
else:
    raise RuntimeError("Spatial Analyst extension is not available.")


def main(
    aoi: str,
    nlcd_1: str,
    nlcd_2: str,
    forest_age_raster: str,
    carbon_ag_bg_us: str,
    carbon_sd_dd_lt: str,
    carbon_so: str,
    forest_lookup_csv: str,
    disturbance_rasters: list,
    cell_size: int,
    year1: int,
    year2: int,
    geography_id: int,
) -> tuple:
    """
    Main function to perform land use change analysis for a given geography.

    Args:
        aoi (str): Path to the Area of Interest feature.
        nlcd_1 (str): Path to NLCD raster for the initial year.
        nlcd_2 (str): Path to NLCD raster for the subsequent year.
        forest_age_raster (str): Path to the forest age raster.
        carbon_ag_bg_us (str): Path to above and below ground carbon raster.
        carbon_sd_dd_lt (str): Path to dead organic matter carbon raster.
        carbon_so (str): Path to soil organic carbon raster.
        forest_lookup_csv (str): Path to the forest lookup CSV file.
        disturbance_rasters (list): List of paths to disturbance rasters.
        cell_size (int): Cell size in meters.
        year1 (int): Initial year.
        year2 (int): Subsequent year.
        geography_id (int): Unique identifier for the geography.

    Returns:
        tuple: DataFrames containing land use results and forest type results.
    """
    try:
        # Save original environment settings
        original_extent = arcpy.env.extent

        # Get the extent of the AOI and input raster
        aoi_extent = arcpy.Describe(aoi).extent
        nlcd_extent = arcpy.Describe(nlcd_1).extent

        # Check if AOI overlaps with NLCD extent
        if aoi_extent.disjoint(nlcd_extent):
            arcpy.AddWarning(
                f"The AOI extent for geography ID {geography_id} does not overlap with the input rasters."
            )
            return None, None

        # Set environment extent to AOI
        arcpy.env.extent = aoi_extent
        arcpy.env.snapRaster = nlcd_1
        arcpy.env.cellSize = cell_size

        arcpy.AddMessage(f"Processing Geography ID: {geography_id}")

        # Step 1: Create stratification raster
        strat_raster = create_landuse_stratification_raster(nlcd_1, nlcd_2, aoi)

        # Step 2: Compute disturbance
        disturbance_wide, disturb_raster = compute_disturbance_max(disturbance_rasters, strat_raster)

        # Step 3: Compute carbon sums
        carbon_df = zonal_sum_carbon(strat_raster, carbon_ag_bg_us, carbon_sd_dd_lt, carbon_so)

        # Merge disturbance and carbon data
        landuse_df = carbon_df.merge(
            disturbance_wide,
            on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
            how="outer",
        )

        # Map NLCD classes to parent classes
        landuse_df["NLCD_1_ParentClass"] = landuse_df["NLCD1_class"].map(nlcd_parent_rollup_categories)
        landuse_df["NLCD_2_ParentClass"] = landuse_df["NLCD2_class"].map(nlcd_parent_rollup_categories)

        # Determine land use category
        landuse_df["Category"] = landuse_df.apply(determine_landuse_category, axis=1)

        # Calculate emissions from forest to non-forest
        landuse_df["Total Emissions Forest to Non Forest CO2"] = landuse_df.apply(
            calculate_forest_to_nonforest_emissions, axis=1
        )

        # Step 4: Tabulate forest age areas
        forest_age_df = tabulate_area_by_stratification(
            strat_raster, forest_age_raster, output_name="ForestAgeTypeRegion"
        )

        # Step 5: Calculate disturbances by forest age
        forest_age_df = calculate_disturbances(disturb_raster, strat_raster, forest_age_raster, forest_age_df)

        # Step 6: Fill NA values and calculate undisturbed area
        forest_age_df = fill_na_values(forest_age_df)

        # Merge with forest lookup table
        forest_age_df = merge_age_factors(forest_age_df, forest_lookup_csv)

        # Step 7: Calculate emissions and removals
        forest_age_df = calculate_forest_removals_and_emissions(forest_age_df, year1, year2)

        return (
            landuse_df.sort_values(by="Hectares", ascending=False),
            forest_age_df.sort_values(by="Hectares", ascending=False),
        )

    except Exception as e:
        arcpy.AddError(f"An error occurred for geography ID {geography_id}: {e}")
        return None, None

    finally:
        # Restore environment settings
        arcpy.env.extent = original_extent


if __name__ == "__main__":
    # Set working directory
    working_directory = os.path.dirname(os.path.abspath(__file__))

    # Define data folders
    data_folder = os.path.join(working_directory, "Data", "Rasters")
    alternate_data_folder = os.path.join(working_directory, "Data", "AlternateData")

    # User inputs
    valid_years = ["2001", "2004", "2006", "2008", "2011", "2013", "2016", "2019", "2021"]
    year1 = input("Enter Year 1: ")
    assert year1 in valid_years, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ")
    assert year2 in valid_years, f"{year2} is not a valid year."

    cell_size = 30

    # Path to the shapefile containing multiple geographies
    aoi_shapefile = os.path.join(working_directory, "Data", "PADUS_BLM_USFS_STATE_PRJ.shp")

    # Field in the shapefile that uniquely identifies each geography
    id_field = "FID"

    # Prepare input configuration
    input_config = {
        "nlcd_1": os.path.join(data_folder, "LandCover", f"NLCD_{year1}_Land_Cover_l48_20210604.tif"),
        "nlcd_2": os.path.join(data_folder, "LandCover", f"NLCD_{year2}_Land_Cover_l48_20210604.tif"),
        "forest_age_raster": os.path.join(data_folder, "ForestType", "forest_raster_07232020.tif"),
        "carbon_ag_bg_us": os.path.join(data_folder, "Carbon", "carbon_ag_bg_us.tif"),
        "carbon_sd_dd_lt": os.path.join(data_folder, "Carbon", "carbon_sd_dd_lt.tif"),
        "carbon_so": os.path.join(data_folder, "Carbon", "carbon_so.tif"),
        "forest_lookup_csv": os.path.join(data_folder, "ForestType", "forest_raster_09172020.csv"),
        "disturbance_rasters": [
            os.path.join(data_folder, "Disturbances", "disturbance_1921.tif")
        ],
        "cell_size": cell_size,
        "year1": int(year1),
        "year2": int(year2),
    }

    start_time = datetime.now()

    # Define output directory
    parent_output_directory = os.path.join(working_directory, "Outputs")
    date_str = start_time.strftime("%Y_%m_%d")
    output_folder_name = f"{date_str}_{year1}_{year2}_BatchProcessing"
    output_path = os.path.join(parent_output_directory, output_folder_name)

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Save input configuration
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(f"Year 1: {year1}\n")
        config_file.write(f"Year 2: {year2}\n")
        config_file.write(f"Cell Size: {cell_size}\n")
        config_file.write(f"Date: {datetime.now()}\n\n")
        config_file.write(str(input_config))

    # Initialize results list
    all_results = []

    # Process each geography
    with arcpy.da.SearchCursor(aoi_shapefile, [id_field, "SHAPE@"]) as cursor:
        for row in cursor:
            geography_id, geometry = row

            arcpy.AddMessage(f"Processing Geography ID: {geography_id}")

            try:
                # Create temporary AOI
                aoi_temp = arcpy.management.CopyFeatures(geometry, "in_memory\\aoi_temp")

                # Update input configuration
                input_config["aoi"] = aoi_temp
                input_config["geography_id"] = geography_id

                # Run main function
                landuse_result, forest_type_result = main(**input_config)

                if landuse_result is None or forest_type_result is None:
                    arcpy.AddWarning(f"Skipping Geography ID {geography_id} due to errors.")
                    continue

                # Summarize GHG emissions/removals
                years_difference = int(year2) - int(year1)
                ghg_result = summarize_ghg(landuse_result, forest_type_result, years_difference)
                ghg_result["Geography_ID"] = geography_id

                all_results.append(ghg_result)

            except Exception as e:
                arcpy.AddError(f"Error processing Geography ID {geography_id}: {e}")
                continue

            finally:
                # Clean up
                arcpy.management.Delete(aoi_temp)
                arcpy.env.extent = None

    # Combine and save results
    if all_results:
        combined_results = pd.concat(all_results, ignore_index=True)
        cols = ["Geography_ID"] + [col for col in combined_results.columns if col != "Geography_ID"]
        combined_results = combined_results[cols]
        combined_results.to_csv(os.path.join(output_path, "combined_results.csv"), index=False)
    else:
        arcpy.AddWarning("No results to save. The results list is empty.")

    arcpy.AddMessage(f"Total processing time: {datetime.now() - start_time}")
