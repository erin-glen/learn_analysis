# forests_analysis.py

import os
from datetime import datetime
import arcpy
import pandas as pd
from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import save_results, summarize_ghg

def main():
    # User inputs
    year1 = input("Enter Year 1: ")
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ")
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    aoi_shapefile = input("Enter path to the AOI shapefile: ")
    id_field = input("Enter the unique ID field in the shapefile: ")

    # Input configuration
    input_config = get_input_config(year1, year2)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    start_time = datetime.now()

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

    # Process each geography
    with arcpy.da.SearchCursor(aoi_shapefile, [id_field, "SHAPE@"]) as cursor:
        for row in cursor:
            geography_id, geometry = row
            arcpy.AddMessage(f"Processing Geography ID: {geography_id}")

            try:
                aoi_temp = arcpy.management.CopyFeatures(geometry, "in_memory\\aoi_temp")
                input_config["aoi"] = aoi_temp
                input_config["geography_id"] = geography_id

                # Perform analysis
                landuse_result, forest_type_result = perform_analysis(
                    input_config, CELL_SIZE, int(year1), int(year2), analysis_type='forest'
                )

                if landuse_result is None or forest_type_result is None:
                    arcpy.AddWarning(f"Skipping Geography ID {geography_id} due to errors.")
                    continue

                years_difference = int(year2) - int(year1)
                ghg_result = summarize_ghg(landuse_result, forest_type_result, years_difference)
                ghg_result["Geography_ID"] = geography_id

                all_results.append(ghg_result)

            except Exception as e:
                arcpy.AddError(f"Error processing Geography ID {geography_id}: {e}")

            finally:
                arcpy.management.Delete(aoi_temp)

    # Combine and save results
    if all_results:
        combined_results = pd.concat(all_results, ignore_index=True)
        combined_results.to_csv(os.path.join(output_path, "combined_results.csv"), index=False)
    else:
        arcpy.AddWarning("No results to save.")

    arcpy.AddMessage(f"Total processing time: {datetime.now() - start_time}")

if __name__ == "__main__":
    main()
