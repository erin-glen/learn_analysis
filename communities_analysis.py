import logging
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

logger = logging.getLogger("CommunityAnalysisLogger")

def round_results_df(df: pd.DataFrame, numeric_columns: list) -> pd.DataFrame:
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2).astype(float)
    return df

def split_emissions_removals(ghg_df: pd.DataFrame) -> pd.DataFrame:
    if "Emissions/Removals" not in ghg_df.columns or "GHG Flux (t CO2e/year)" not in ghg_df.columns:
        return ghg_df

    ghg_df["Emissions"] = 0.0
    ghg_df["Removals"] = 0.0

    flux_numeric = pd.to_numeric(ghg_df["GHG Flux (t CO2e/year)"], errors='coerce').fillna(0)
    ghg_df.loc[ghg_df["Emissions/Removals"] == "Emissions", "Emissions"] = flux_numeric
    ghg_df.loc[ghg_df["Emissions/Removals"] == "Removals", "Removals"] = flux_numeric

    return ghg_df

def create_ipcc_summary(ghg_df: pd.DataFrame) -> pd.DataFrame:
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
    csv_str = df.to_csv(index=index)
    csv_str = csv_str.replace('\r\n', '\n').replace('\r', '')
    return csv_str

def add_total_row_ghg(ghg_df: pd.DataFrame) -> pd.DataFrame:
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
    if landuse_matrix.index.name is not None:
        landuse_matrix.reset_index(inplace=True)

    col_title = f"{year2}: Across\n{year1}: Down"
    if "NLCD1_class" in landuse_matrix.columns:
        landuse_matrix.rename(columns={"NLCD1_class": ""}, inplace=True)
    landuse_matrix.columns.name = None

    # Tree canopy summary
    tree_canopy_summary.reset_index(drop=True, inplace=True)

    # GHG inventory
    ghg_inventory.reset_index(drop=True, inplace=True)
    ghg_inventory = add_total_row_ghg(ghg_inventory)
    cols_to_drop = ["GHG Flux (t CO2e/year)", "IPCC_Category", "Factor (t C/ha for emissions, t C/ha/yr for removals)"]
    ghg_inventory.drop(columns=cols_to_drop, inplace=True, errors="ignore")

    # IPCC summary
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
    logger.info("Starting standalone communities_analysis main().")
    year1 = input("Enter Year 1: ").strip()
    assert year1 in VALID_YEARS, f"{year1} is not a valid year."

    year2 = input("Enter Year 2: ").strip()
    assert year2 in VALID_YEARS, f"{year2} is not a valid year."

    aoi_name = input("Enter the AOI name: ").strip()
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local): ").strip()
    assert tree_canopy_source in ["NLCD", "CBW", "Local"], f"{tree_canopy_source} is not valid."

    recat_response = input("Enable recategorization mode? (y/n): ").strip().lower()
    recategorize_mode = (recat_response == 'y')

    input_config = get_input_config(year1, year2, aoi_name, tree_canopy_source)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    emissions_factor = input_config.get("emissions_factor")
    removals_factor = input_config.get("removals_factor")
    c_to_co2 = input_config.get("c_to_co2", 44 / 12)
    if emissions_factor is None or removals_factor is None:
        logger.error("Missing required TOF emissions_factor or removals_factor.")
        raise ValueError("Emissions factor and removals factor must be provided.")

    start_time = dt.now()
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder_name = f"{year1}_{year2}_{aoi_name}_{timestamp}"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save config
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
        arcpy.AddError("Analysis failed; see log or ArcPy messages.")
        logger.error("Analysis returned None.")
        return

    # Save raw DataFrames
    landuse_csv = os.path.join(output_path, f"landuse_result_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result_{timestamp}.csv")
    landuse_result.to_csv(landuse_csv, index=False)
    forest_type_result.to_csv(forest_type_csv, index=False)

    years_diff = int(year2) - int(year1)
    landuse_matrix = create_land_cover_transition_matrix(landuse_result)
    tree_canopy_df = summarize_tree_canopy(landuse_result)

    from funcs import summarize_ghg
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

    # Final summary
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

    logger.info(f"Summary written to: {summary_csv}")
    arcpy.AddMessage(f"Summary written to: {summary_csv}")
    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")
    logger.info("communities_analysis main() complete.")

if __name__ == "__main__":
    # If you want, you can also do a quick logger setup here:
    import sys
    import logging

    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="[%(levelname)s] %(message)s"
    )

    main()
