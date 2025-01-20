# longleaf_analysis.py

import os
from datetime import datetime as dt
import arcpy
import pandas as pd

# Reuse the config and analysis_core modules (already in your project)
from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis

# Reuse your helper functions (save_results, summarize_ghg, etc.)
from funcs import save_results, summarize_ghg


def main(mode=None):
    """
    Main function to execute longleaf analysis on two AOIs, each containing exactly one polygon.

    Args:
        mode (str, optional): Mode of operation.
                              - 'test' to run in test mode (you can decide how to handle 'test' here).
                              - 'recategorize' to enable recategorization based on disturbances.
                              Defaults to None.
    """
    # Prompt user for years (like in forests_analysis.py)
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    # Example AOI shapefiles (each has exactly one polygon)
    aoi_shapefile_1 = r"C:\GIS\Data\USA\longleaf_pine\LLP_gulfcoastalplain_multipart\LLP_gulfcoastalplain_multipart\LLP_gulfcoastalplain_multipart.shp"
    aoi_shapefile_2 = r"C:\GIS\Data\USA\longleaf_pine\LLP_desotocampshelby_multipart\LLP_desotocampshelby_multipart\LLP_desotocampshelby_multipart.shp"

    # Load input configuration (reuse your existing get_input_config)
    input_config = get_input_config(year1, year2)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    # Retrieve emissions/removals factors from the config
    emissions_factor = input_config.get("emissions_factor", None)
    removals_factor = input_config.get("removals_factor", None)
    c_to_co2 = input_config.get("c_to_co2", 44 / 12)  # Default to 44/12 if not provided

    # Validate factors
    if emissions_factor is None or removals_factor is None:
        raise ValueError("Emissions factor and removals factor must be provided in the configuration.")

    # Output directory
    start_time = dt.now()
    date_str = start_time.strftime("%Y_%m_%d")
    output_folder_name = f"{date_str}_{year1}_{year2}_LongleafAnalysis"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save the current configuration for reference
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(str(input_config))

    # Determine the recategorize_mode flag
    recategorize_mode = (mode == 'recategorize')
    if recategorize_mode:
        arcpy.AddMessage("Recategorization mode is enabled.")

    # We'll store GHG summary DataFrames for each AOI in this list
    all_ghg_results = []

    # Process each AOI shapefile (exactly one polygon in each shapefile)
    for i, aoi_path in enumerate([aoi_shapefile_1, aoi_shapefile_2], start=1):
        arcpy.AddMessage(f"Processing AOI #{i}: {aoi_path}")
        aoi_temp = None
        try:
            # Copy the single polygon into in_memory for analysis
            aoi_temp = arcpy.management.CopyFeatures(aoi_path, f"in_memory\\aoi_temp_{i}")

            # Update input_config with this AOI
            input_config["aoi"] = aoi_temp
            # Optionally track which AOI is which
            input_config["longleaf_id"] = f"AOI_{i}"

            # Perform the core analysis (analysis_type can be "forest" or "community" as you wish)
            landuse_result, forest_type_result = perform_analysis(
                input_config,
                CELL_SIZE,
                int(year1),
                int(year2),
                analysis_type='forest',  # or 'community', depending on your needs
                tree_canopy_source=None,
                recategorize_mode=recategorize_mode
            )

            # If there was an error, skip
            if landuse_result is None or forest_type_result is None:
                arcpy.AddWarning(f"Skipping AOI #{i} due to errors.")
                continue

            # Summarize GHG
            years_difference = int(year2) - int(year1)
            ghg_result = summarize_ghg(
                landuse_df=landuse_result,
                forest_type_df=forest_type_result,
                years=years_difference,
                emissions_factor=emissions_factor,
                removals_factor=removals_factor,
                c_to_co2=c_to_co2,
                include_trees_outside_forest=False
            )

            # Add an identifier so we know which AOI this came from
            ghg_result["AOI_ID"] = f"AOI_{i}"

            # Append to our list of results
            all_ghg_results.append(ghg_result)

            # (Optional) Save the results for each AOI
            # you can adjust the function signature for save_results if needed
            save_results(
                landuse_result,
                forest_type_result,
                output_path,
                start_time,
                geography_id=f"AOI_{i}"
            )

        except Exception as e:
            arcpy.AddError(f"Error processing AOI #{i}: {e}")

        finally:
            if aoi_temp:
                arcpy.management.Delete(aoi_temp)

        # If we had a 'test' mode, we could break after the first AOI. Up to you.
        if mode == 'test':
            arcpy.AddMessage("Test mode enabled. Processed only the first AOI.")
            break

    # Combine and save final GHG results
    if all_ghg_results:
        combined_ghg = pd.concat(all_ghg_results, ignore_index=True)
        combined_ghg.to_csv(os.path.join(output_path, "longleaf_combined_ghg.csv"), index=False)
    else:
        arcpy.AddWarning("No GHG results were generated.")

    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")


if __name__ == "__main__":
    # Example: to enable recategorize mode, call main('recategorize')
    # to enable test mode, call main('test')
    # or call main() for normal execution
    main()
