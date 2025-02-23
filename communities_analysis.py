# communities_analysis.py

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


def round_results_df(df: pd.DataFrame, numeric_columns: list) -> pd.DataFrame:
    """
    Rounds specified numeric columns in the DataFrame to two decimals and converts them to float.
    Any non-finite values (e.g., NaN) are replaced with 0.
    """
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2).astype(float)
    return df


def split_emissions_removals(ghg_df: pd.DataFrame) -> pd.DataFrame:
    """
    If you have a single "Emissions/Removals" column plus "GHG Flux (t CO2e/year)",
    split them into two numeric columns, "Emissions" and "Removals".
    """
    if "Emissions/Removals" not in ghg_df.columns or "GHG Flux (t CO2e/year)" not in ghg_df.columns:
        return ghg_df

    ghg_df["Emissions"] = 0.0
    ghg_df["Removals"] = 0.0

    flux_numeric = pd.to_numeric(ghg_df["GHG Flux (t CO2e/year)"], errors='coerce').fillna(0)
    ghg_df.loc[ghg_df["Emissions/Removals"] == "Emissions", "Emissions"] = flux_numeric
    ghg_df.loc[ghg_df["Emissions/Removals"] == "Removals", "Removals"] = flux_numeric

    return ghg_df


def create_ipcc_summary(ghg_df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarize net flux by IPCC categories, then append a 'Total' row.
    Relies on 'GHG Flux (t CO2e/year)' for the net flux values.
    """

    def map_ipcc_category(row):
        cat = row.get("Category", "")
        typ = row.get("Type", "")
        if cat == "Forest Remaining Forest":
            return "Forest remaining forest"
        elif cat == "Forest Remaining Forest (fire)":
            # We'll treat 'Forest Remaining Forest (fire)' as 'Forest remaining forest' too
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
    summary = pd.concat([
        summary,
        pd.DataFrame({"IPCC_Category": ["Total"], "Net Flux (t CO2e/yr)": [total_flux]})
    ], ignore_index=True)

    summary.rename(columns={"IPCC_Category": "Category"}, inplace=True)
    return summary


def dataframe_as_csv_string(df: pd.DataFrame, index: bool = False) -> str:
    """
    Convert to CSV text without extra blank lines on Windows.
    """
    csv_str = df.to_csv(index=index)
    csv_str = csv_str.replace('\r\n', '\n').replace('\r', '')
    return csv_str


def add_total_row_ghg(ghg_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a final 'Total' row summing numeric columns 'Emissions' and 'Removals'.
    """
    if "Removals" not in ghg_df.columns or "Emissions" not in ghg_df.columns:
        return ghg_df

    sum_removals = pd.to_numeric(ghg_df["Removals"], errors='coerce').fillna(0).sum()
    sum_emissions = pd.to_numeric(ghg_df["Emissions"], errors='coerce').fillna(0).sum()

    total_row = {
        "Category": "Total",
        "Area (ha, total)": "N/A",
        "Emissions": sum_emissions,
        "Removals": sum_removals,
    }

    return pd.concat([ghg_df, pd.DataFrame([total_row])], ignore_index=True)


def write_enhanced_summary_csv(
        output_csv_path: str,
        landuse_matrix: pd.DataFrame,
        tree_canopy_summary: pd.DataFrame,
        ghg_inventory: pd.DataFrame,
        ipcc_summary: pd.DataFrame,
        year1: str,
        year2: str
):
    """
    Write 4 major tables to a single CSV, each separated by *5* blank lines:
      1) Land Use Change Matrix (hectares)
      2) Tree Canopy Summary
      3) GHG Inventory (with separate Emissions & Removals)
      4) Simplified report: IPCC categories
    """
    # Land Use matrix: reset index if needed
    if landuse_matrix.index.name is not None:
        landuse_matrix.reset_index(inplace=True)

    col_title = f"{year2}: Across\n{year1}: Down"
    if "NLCD1_class" in landuse_matrix.columns:
        landuse_matrix.rename(columns={"NLCD1_class": ""}, inplace=True)
    landuse_matrix.columns.name = None

    # Clean up indexes
    tree_canopy_summary.reset_index(drop=True, inplace=True)
    ghg_inventory.reset_index(drop=True, inplace=True)
    ipcc_summary.reset_index(drop=True, inplace=True)

    # Insert total row, if not already there
    ghg_inventory = add_total_row_ghg(ghg_inventory)

    # Drop columns not needed in final CSV
    cols_to_drop = [
        "GHG Flux (t CO2e/year)",
        "IPCC_Category",
        "Factor (t C/ha for emissions, t C/ha/yr for removals)",
    ]
    ghg_inventory.drop(columns=cols_to_drop, inplace=True, errors="ignore")

    # Write out the CSV
    with open(output_csv_path, "w", encoding="utf-8", newline='') as f:
        # 1) Land Use Change Matrix
        f.write("Land Use Change Matrix (hectares)\n")
        f.write(col_title + "\n")
        f.write(dataframe_as_csv_string(landuse_matrix, index=False))
        f.write("\n" * 5)

        # 2) Tree Canopy Summary
        f.write("Tree Canopy Summary\n")
        f.write(dataframe_as_csv_string(tree_canopy_summary, index=False))
        f.write("\n" * 5)

        # 3) GHG Inventory
        f.write("GHG Inventory with separated emissions and removals (t CO2e/yr)\n")
        f.write(dataframe_as_csv_string(ghg_inventory, index=False))
        f.write("\n" * 5)

        # 4) IPCC Summary
        f.write("Simplified report: IPCC categories\n")
        f.write(dataframe_as_csv_string(ipcc_summary, index=False))
        f.write("\n" * 5)


###############################################
# >>> NEW FUNCTION: subtract recategorized amounts from forest to grassland
###############################################
def subtract_recat_from_forest_to_grass(ghg_df: pd.DataFrame, forest_type_df: pd.DataFrame) -> pd.DataFrame:
    """
    After normal GHG calculation, subtract area/emissions that were recategorized
    from 'Forest to Grassland' to 'Forest Remaining Forest (fire)'.

    We'll sum up 'fire_HA' and 'Annual_Emissions_Fire_CO2' for rows in forest_type_df
    where Category == 'Forest Remaining Forest (fire)'. Then we subtract that from the
    'Forest to Grassland' line in the GHG DataFrame.
    """
    # Sum of re-labeled area
    recat_area = forest_type_df.loc[
        forest_type_df["Category"] == "Forest Remaining Forest (fire)",
        "fire_HA"
    ].sum()

    # Sum of re-labeled emissions
    recat_emissions = forest_type_df.loc[
        forest_type_df["Category"] == "Forest Remaining Forest (fire)",
        "Annual_Emissions_Fire_CO2"
    ].sum()

    # If nothing to subtract, skip
    if (recat_area == 0) and (recat_emissions == 0):
        return ghg_df

    # Identify the row in ghg_df for "Forest to Grassland" / "Emissions"
    mask = (
            (ghg_df["Category"] == "Forest Change") &
            (ghg_df["Type"] == "To Grassland") &
            (ghg_df["Emissions/Removals"] == "Emissions")
    )

    if not mask.any():
        return ghg_df  # no matching row found

    # Subtract area/emissions from that row
    ghg_df.loc[mask, "Area (ha, total)"] = (
            ghg_df.loc[mask, "Area (ha, total)"] - recat_area
    )
    ghg_df.loc[mask, "Emissions"] = (
            ghg_df.loc[mask, "Emissions"] - recat_emissions
    )

    return ghg_df


def main():
    # 1) User Inputs
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    aoi_name = input("Enter the AOI name: ").strip()
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local): ").strip()
    assert tree_canopy_source in ["NLCD", "CBW", "Local"], f"{tree_canopy_source} is not valid."

    # Prompt for recategorization
    recat_response = input("Enable recategorization mode? (y/n): ").strip().lower()
    recategorize_mode = (recat_response == 'y')

    # 2) Build Input Config
    input_config = get_input_config(year1, year2, aoi_name, tree_canopy_source)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    emissions_factor = input_config.get("emissions_factor")
    removals_factor = input_config.get("removals_factor")
    c_to_co2 = input_config.get("c_to_co2", 44 / 12)
    if emissions_factor is None or removals_factor is None:
        raise ValueError("Emissions factor and removals factor must be provided.")

    start_time = dt.now()

    # 3) Output folder
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder_name = f"{year1}_{year2}_{aoi_name}_{timestamp}"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save config
    with open(os.path.join(output_path, "config.txt"), "w") as cf:
        cf.write(str(input_config))

    # 4) Perform analysis
    landuse_result, forest_type_result = perform_analysis(
        input_config,
        CELL_SIZE,
        int(year1),
        int(year2),
        analysis_type='community',
        tree_canopy_source=tree_canopy_source,
        recategorize_mode=recategorize_mode
    )

    if landuse_result is None or forest_type_result is None:
        arcpy.AddError("Analysis failed.")
        return

    # Save raw outputs
    landuse_csv = os.path.join(output_path, f"landuse_result_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result_{timestamp}.csv")
    landuse_result.to_csv(landuse_csv, index=False)
    forest_type_result.to_csv(forest_type_csv, index=False)

    # 5) Summaries
    years_diff = int(year2) - int(year1)

    # Land use matrix
    landuse_matrix = create_land_cover_transition_matrix(landuse_result)
    # Tree canopy
    tree_canopy_df = summarize_tree_canopy(landuse_result)

    # Summarize GHG
    ghg_df = summarize_ghg(
        landuse_df=landuse_result,
        forest_type_df=forest_type_result,
        years=years_diff,
        emissions_factor=emissions_factor,
        removals_factor=removals_factor,
        c_to_co2=c_to_co2,
    )
    ghg_df = split_emissions_removals(ghg_df)

    # 6) Subtract recategorized area/emissions if recategorize_mode = True
    if recategorize_mode:
        ghg_df = subtract_recat_from_forest_to_grass(ghg_df, forest_type_result)

    # 7) Now re-round the final columns for presentation
    #    (Area, Emissions, Removals) to zero decimals as requested
    for col in ["Area (ha, total)", "Emissions", "Removals"]:
        if col in ghg_df.columns:
            ghg_df[col] = pd.to_numeric(ghg_df[col], errors="coerce").fillna(0)
            # Use 0 decimals
            ghg_df[col] = ghg_df[col].round(0).astype(int)

    # IPCC summary
    ipcc_df = create_ipcc_summary(ghg_df)

    # 8) Final summary CSV
    summary_csv = os.path.join(output_path, f"summary_{timestamp}.csv")
    write_enhanced_summary_csv(
        summary_csv,
        landuse_matrix,
        tree_canopy_df,
        ghg_df,
        ipcc_df,
        year1,
        year2
    )

    arcpy.AddMessage(f"Summary written to: {summary_csv}")
    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")


if __name__ == "__main__":
    main()
