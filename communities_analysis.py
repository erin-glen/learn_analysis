import os
from datetime import datetime as dt
import arcpy
import pandas as pd

from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import (
    save_results,
    summarize_ghg,
    summarize_tree_canopy,
    create_land_cover_transition_matrix,
)


def create_ipcc_summary(ghg_df: pd.DataFrame) -> pd.DataFrame:
    """Same as before."""

    def map_ipcc_category(row):
        cat = row["Category"]
        typ = row["Type"]
        if cat == "Forest Remaining Forest":
            return "Forest remaining forest"
        elif cat == "Forest Change":
            if "To " in typ:
                return "Forest to nonforest"
            elif "Reforestation" in typ:
                return "Nonforest to forest"
        elif cat == "Trees Outside Forest":
            return "Trees outside forests"
        return "Other"

    ghg_df["IPCC_Category"] = ghg_df.apply(map_ipcc_category, axis=1)
    summary = (
        ghg_df.groupby("IPCC_Category")["GHG Flux (t CO2e/year)"]
        .sum()
        .reset_index()
        .rename(columns={"GHG Flux (t CO2e/year)": "Net Flux (t CO2e/yr)"})
    )
    total_flux = summary["Net Flux (t CO2e/yr)"].sum()
    total_row = pd.DataFrame({"IPCC_Category": ["Total"], "Net Flux (t CO2e/yr)": [total_flux]})
    summary = pd.concat([summary, total_row], ignore_index=True)
    summary.rename(columns={"IPCC_Category": "Category"}, inplace=True)
    return summary


def dataframe_as_csv_string(df: pd.DataFrame, index: bool = False) -> str:
    """
    Convert a DataFrame to a CSV-formatted string without extra blank lines.

    1) Use df.to_csv(...) without 'line_terminator'.
    2) Replace all '\r\n' with '\n', and strip out any leftover '\r'.
    """
    csv_str = df.to_csv(index=index)  # No line_terminator
    # Replace Windows-style CRLF with LF, and remove stray CR
    csv_str = csv_str.replace('\r\n', '\n').replace('\r', '')
    return csv_str


def write_enhanced_summary_csv(
        output_csv_path: str,
        landuse_matrix: pd.DataFrame,
        tree_canopy_summary: pd.DataFrame,
        ghg_inventory: pd.DataFrame,
        ipcc_summary: pd.DataFrame
):
    """
    Write four major tables to a single CSV with headings and exactly one blank line
    separating each table. Also remove numeric indices from each DataFrame.
    """
    # Remove numeric indices
    if landuse_matrix.index.name is not None:
        landuse_matrix = landuse_matrix.reset_index()
    # Optionally rename the pivot's first column if needed
    if "NLCD1_class" in landuse_matrix.columns:
        landuse_matrix.rename(columns={"NLCD1_class": ""}, inplace=True)

    tree_canopy_summary.reset_index(drop=True, inplace=True)
    ghg_inventory.reset_index(drop=True, inplace=True)
    ipcc_summary.reset_index(drop=True, inplace=True)

    with open(output_csv_path, "w", encoding="utf-8", newline='') as f:
        # 1) Land Use Change Matrix
        f.write("Land Use Change Matrix (hectares)\n")
        f.write(dataframe_as_csv_string(landuse_matrix, index=False))
        f.write("\n")  # single blank line

        # 2) Tree Canopy Summary
        f.write("Tree Canopy Summary\n")
        f.write(dataframe_as_csv_string(tree_canopy_summary, index=False))
        f.write("\n")

        # 3) GHG Inventory
        f.write("GHG Inventory with separated emissions and removals (t CO2e/yr)\n")
        f.write(dataframe_as_csv_string(ghg_inventory, index=False))
        f.write("\n")

        # 4) IPCC Summary
        f.write("Simplified report: IPCC categories\n")
        f.write(dataframe_as_csv_string(ipcc_summary, index=False))
        f.write("\n")


def main():
    # (Same as before)
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."
    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."
    aoi_name = input("Enter the AOI name: ").strip()
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local): ").strip()
    assert tree_canopy_source in ["NLCD", "CBW", "Local"], f"{tree_canopy_source} is not valid."

    # Build input config
    input_config = get_input_config(year1, year2, aoi_name, tree_canopy_source)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    emissions_factor = input_config.get("emissions_factor")
    removals_factor = input_config.get("removals_factor")
    c_to_co2 = input_config.get("c_to_co2", 44 / 12)
    if emissions_factor is None or removals_factor is None:
        raise ValueError("Emissions factor and removals factor must be provided in the configuration.")

    start_time = dt.now()

    # Output folder
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")
    output_folder_name = f"{date_str}_{year1}_{year2}_{aoi_name}"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save config
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(str(input_config))

    # Run analysis
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

    # Save raw results if desired
    save_results(landuse_result, forest_type_result, output_path, start_time)

    # Summaries
    years_difference = int(year2) - int(year1)
    landuse_matrix = create_land_cover_transition_matrix(landuse_result)
    landuse_matrix.columns.name = f"{year2}: Across\n{year1}: Down"  # optional

    tree_canopy_summary = summarize_tree_canopy(landuse_result)

    ghg_inventory = summarize_ghg(
        landuse_df=landuse_result,
        forest_type_df=forest_type_result,
        years=years_difference,
        emissions_factor=emissions_factor,
        removals_factor=removals_factor,
        c_to_co2=c_to_co2,
    )

    ipcc_summary = create_ipcc_summary(ghg_inventory)

    # Write all in one file
    enhanced_summary_csv = os.path.join(output_path, "enhanced_summary.csv")
    write_enhanced_summary_csv(
        enhanced_summary_csv,
        landuse_matrix,
        tree_canopy_summary,
        ghg_inventory,
        ipcc_summary
    )

    arcpy.AddMessage(f"Enhanced summary written to: {enhanced_summary_csv}")
    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")


if __name__ == "__main__":
    main()
