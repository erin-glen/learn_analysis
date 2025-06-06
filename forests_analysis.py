# forests_analysis.py

import os
from datetime import datetime as dt
import arcpy
import pandas as pd
from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import save_results, summarize_ghg

def main(mode=None):
    """
    Main function to execute forest analysis.

    Args:
        mode (str, optional): Mode of operation.
                              - 'test' to run in test mode.
                              - 'recategorize' to enable recategorization based on disturbances.
                              Defaults to None.
    """
    # User inputs
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    # Hardcoded AOI shapefile path and unique ID field
    aoi_shapefile = r"C:\GIS\Data\LEARN\SourceData\AOI\PADUS_BLM_USFS_STATE_PRJ.shp"
    id_field = "FID"

    # Input configuration
    input_config = get_input_config(year1, year2)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    # Retrieve emissions_factor and removals_factor from input_config
    emissions_factor = input_config.get("emissions_factor", None)
    removals_factor = input_config.get("removals_factor", None)
    c_to_co2 = input_config.get("c_to_co2", 44 / 12)  # Default value if not provided

    # Ensure that emissions_factor and removals_factor are provided
    if emissions_factor is None or removals_factor is None:
        raise ValueError("Emissions factor and removals factor must be provided in the configuration.")

    start_time = dt.now()

    # Output directory
    date_str = start_time.strftime("%Y_%m_%d")
    output_folder_name = f"{date_str}_{year1}_{year2}_BatchProcessing"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save configuration
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(str(input_config))

    # Initialize results list
    all_results = []

    # Determine the recategorize_mode flag based on the mode parameter
    recategorize_mode = False
    if mode == 'recategorize':
        recategorize_mode = True
        arcpy.AddMessage("Recategorization mode is enabled.")
    elif mode == 'test':
        arcpy.AddMessage("Test mode is enabled. Only the first geography will be processed.")

    # Process each geography
    with arcpy.da.SearchCursor(aoi_shapefile, [id_field, "SHAPE@"]) as cursor:
        for idx, row in enumerate(cursor):
            geography_id, geometry = row
            arcpy.AddMessage(f"Processing Geography ID: {geography_id}")

            try:
                aoi_temp = arcpy.management.CopyFeatures(geometry, "in_memory\\aoi_temp")
                input_config["aoi"] = aoi_temp
                input_config["geography_id"] = geography_id

                # Perform analysis with the recategorize_mode flag
                landuse_result, forest_type_result = perform_analysis(
                    input_config,
                    CELL_SIZE,
                    int(year1),
                    int(year2),
                    analysis_type='forest',
                    tree_canopy_source=None,  # Set to None or appropriate value if needed
                    recategorize_mode=recategorize_mode  # Pass the new flag
                )

                if landuse_result is None or forest_type_result is None:
                    arcpy.AddWarning(f"Skipping Geography ID {geography_id} due to errors.")
                    continue

                years_difference = int(year2) - int(year1)

                # Pass new parameters to summarize_ghg
                ghg_result = summarize_ghg(
                    landuse_df=landuse_result,
                    forest_type_df=forest_type_result,
                    years=years_difference,
                    emissions_factor=emissions_factor,
                    removals_factor=removals_factor,
                    c_to_co2=c_to_co2,
                    include_trees_outside_forest=False,  # Exclude Trees Outside Forest categories
                )
                ghg_result["Geography_ID"] = geography_id

                all_results.append(ghg_result)

                # Save individual results (optional)
                save_results(
                    landuse_result,
                    forest_type_result,
                    output_path,
                    start_time,
                    geography_id=geography_id
                )

            except Exception as e:
                arcpy.AddError(f"Error processing Geography ID {geography_id}: {e}")

            finally:
                arcpy.management.Delete(aoi_temp)

            # If in test mode, process only the first feature
            if mode == 'test':
                arcpy.AddMessage("Test mode enabled. Processed only the first feature.")
                break

    # Combine and save results
    if all_results:
        combined_results = pd.concat(all_results, ignore_index=True)
        combined_results.to_csv(os.path.join(output_path, "combined_results.csv"), index=False)
    else:
        arcpy.AddWarning("No results to save.")

    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")

if __name__ == "__main__":
    # To run in recategorize mode, call main('recategorize')
    # To run in test mode, call main('test')
    # To run normally, call main()
    main('recategorize')  # Example: enable recategorize mode
