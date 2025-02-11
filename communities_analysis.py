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
    Rounds specified numeric columns in the DataFrame to whole numbers and converts them to int.
    Any non-finite values (e.g., NaN) are replaced with 0 to avoid casting errors.
    This helps ensure Excel will interpret them as numbers rather than text.
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
        elif cat == "Forest Change":
            if "To " in typ:
                return "Forest to nonforest"
            elif "Reforestation" in typ:
                return "Nonforest to forest"
        elif cat == "Trees Outside Forest":
            return "Trees outside forests"
        return "Other"

    # 1) Map IPCC category
    ghg_df["IPCC_Category"] = ghg_df.apply(map_ipcc_category, axis=1)

    # 2) Group by IPCC_Category and sum up "GHG Flux (t CO2e/year)"
    summary = (
        ghg_df.groupby("IPCC_Category")["GHG Flux (t CO2e/year)"]
              .sum()
              .reset_index()
              .rename(columns={"GHG Flux (t CO2e/year)": "Net Flux (t CO2e/yr)"})
    )

    # 3) Add a "Total" row
    total_flux = summary["Net Flux (t CO2e/yr)"].sum()
    summary = pd.concat([
        summary,
        pd.DataFrame({"IPCC_Category": ["Total"], "Net Flux (t CO2e/yr)": [total_flux]})
    ], ignore_index=True)

    # 4) Rename the IPCC_Category column to "Category"
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
    Add a final 'Total' row summing numeric columns 'Removals' and 'Emissions'.
    """
    if "Removals" not in ghg_df.columns or "Emissions" not in ghg_df.columns:
        return ghg_df

    sum_removals = pd.to_numeric(ghg_df["Removals"], errors='coerce').fillna(0).sum()
    sum_emissions = pd.to_numeric(ghg_df["Emissions"], errors='coerce').fillna(0).sum()

    total_row = {
        "Category": "Total",
        "Area (ha, total)": "N/A",
        "Removals": sum_removals,
        "Emissions": sum_emissions,
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
      1) Land Use Change Matrix
      2) Tree Canopy Summary
      3) GHG Inventory (with separate Emissions & Removals)
      4) IPCC Summary
    """
    if landuse_matrix.index.name is not None:
        landuse_matrix.reset_index(inplace=True)

    col_title = f"{year2}: Across\n{year1}: Down"
    if "NLCD1_class" in landuse_matrix.columns:
        landuse_matrix.rename(columns={"NLCD1_class": ""}, inplace=True)
    landuse_matrix.columns.name = None

    tree_canopy_summary.reset_index(drop=True, inplace=True)
    ghg_inventory.reset_index(drop=True, inplace=True)
    ghg_inventory = add_total_row_ghg(ghg_inventory)
    cols_to_drop = ["GHG Flux (t CO2e/year)", "IPCC_Category", "Factor (t C/ha for emissions, t C/ha/yr for removals)"]
    ghg_inventory.drop(columns=cols_to_drop, inplace=True, errors="ignore")
    ipcc_summary.reset_index(drop=True, inplace=True)

    with open(output_csv_path, "w", encoding="utf-8", newline='') as f:
        f.write("Land Use Change Matrix (hectares)\n")
        f.write(col_title + "\n")
        f.write(dataframe_as_csv_string(landuse_matrix, index=False))
        f.write("\n" * 5)

        f.write("Tree Canopy Summary\n")
        f.write(dataframe_as_csv_string(tree_canopy_summary, index=False))
        f.write("\n" * 5)

        f.write("GHG Inventory with separated emissions and removals (t CO2e/yr)\n")
        f.write(dataframe_as_csv_string(ghg_inventory, index=False))
        f.write("\n" * 5)

        f.write("Simplified report: IPCC categories\n")
        f.write(dataframe_as_csv_string(ipcc_summary, index=False))
        f.write("\n" * 5)


def main():
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    aoi_name = input("Enter the AOI name: ").strip()
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local): ").strip()
    assert tree_canopy_source in ["NLCD", "CBW", "Local"], f"{tree_canopy_source} is not valid."

    recategorize_input = input("Enable recategorization mode? (y/n): ").strip().lower()
    recategorize_mode = recategorize_input.startswith('y')

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
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder_name = f"{year1}_{year2}_{aoi_name}_{timestamp}"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    with open(os.path.join(output_path, "config.txt"), "w") as cf:
        cf.write(str(input_config))

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

    landuse_numeric_cols = [
        "Hectares", "CellCount",
        "carbon_ag_bg_us", "carbon_sd_dd_lt", "carbon_so",
        "fire_HA", "harvest_HA", "insect_damage_HA",
        "TreeCanopy_HA", "TreeCanopyLoss_HA",
        "Annual Emissions Forest to Non Forest CO2",
    ]
    forest_type_numeric_cols = [
        "Hectares", "fire_HA", "harvest_HA", "insect_damage_HA", "undisturbed_HA",
        "Annual_Removals_Undisturbed_CO2", "Annual_Removals_N_to_F_CO2",
        "Annual_Emissions_Fire_CO2", "Annual_Emissions_Harvest_CO2", "Annual_Emissions_Insect_CO2"
    ]
    landuse_result = round_results_df(landuse_result, landuse_numeric_cols)
    forest_type_result = round_results_df(forest_type_result, forest_type_numeric_cols)

    landuse_csv = os.path.join(output_path, f"landuse_result_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result_{timestamp}.csv")
    landuse_result.to_csv(landuse_csv, index=False)
    forest_type_result.to_csv(forest_type_csv, index=False)

    years_diff = int(year2) - int(year1)
    landuse_matrix = create_land_cover_transition_matrix(landuse_result)
    tree_canopy_df = summarize_tree_canopy(landuse_result)

    ghg_df = summarize_ghg(
        landuse_df=landuse_result,
        forest_type_df=forest_type_result,
        years=years_diff,
        emissions_factor=emissions_factor,
        removals_factor=removals_factor,
        c_to_co2=c_to_co2,
    )
    ghg_df = split_emissions_removals(ghg_df)
    ipcc_df = create_ipcc_summary(ghg_df)

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
