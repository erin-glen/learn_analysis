import os
from datetime import datetime as dt
import arcpy
from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import (
    save_results,
    write_dataframes_to_csv,
    summarize_ghg,
    summarize_tree_canopy,
    create_land_cover_transition_matrix,
)

def main():
    # User inputs
    year1 = input("Enter Year 1: ")
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ")
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    aoi_name = input("Enter the AOI name: ")
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local): ")
    assert tree_canopy_source in ["NLCD", "CBW", "Local"], f"{tree_canopy_source} is not valid."

    # Input configuration
    input_config = get_input_config(year1, year2, aoi_name, tree_canopy_source)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    start_time = dt.now()

    # Output directory
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")  # Updated to include hour and minute
    output_folder_name = f"{date_str}_{year1}_{year2}_{aoi_name}"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save configuration
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(str(input_config))

    # Perform analysis
    landuse_result, forest_type_result = perform_analysis(
        input_config,
        CELL_SIZE,
        int(year1),
        int(year2),
        analysis_type='community',
        tree_canopy_source=tree_canopy_source
    )

    if landuse_result is None or forest_type_result is None:
        arcpy.AddError("Analysis failed.")
        return

    # Save results
    save_results(landuse_result, forest_type_result, output_path, start_time)

    # Summarize results
    years_difference = int(year2) - int(year1)
    tc_summary = summarize_tree_canopy(landuse_result)
    transition_matrix = create_land_cover_transition_matrix(landuse_result)
    ghg_result = summarize_ghg(landuse_result, forest_type_result, years_difference)

    # Save summaries
    df_list = [transition_matrix, tc_summary, ghg_result]
    csv_file_path = os.path.join(output_path, "summary.csv")
    write_dataframes_to_csv(df_list, csv_file_path, space=5)

    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")

if __name__ == "__main__":
    main()
