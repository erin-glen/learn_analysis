import os
import arcpy
import pandas as pd
from datetime import datetime as dt

from config import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import (
    summarize_ghg,
    summarize_tree_canopy,
    create_land_cover_transition_matrix,
)
from communities_analysis import (
    split_emissions_removals,
    create_ipcc_summary,
    write_enhanced_summary_csv,
)


def calculate_gross_net_flux(ghg_df: pd.DataFrame) -> tuple:
    """
    Calculate gross emissions, gross removals, and net flux from a GHG DataFrame
    that already has numeric "Emissions" and "Removals" columns
    (after calling 'split_emissions_removals').
    """
    if "Emissions" not in ghg_df.columns or "Removals" not in ghg_df.columns:
        return (0.0, 0.0, 0.0)

    # Sum positive emission values
    gross_emissions = ghg_df["Emissions"].where(ghg_df["Emissions"] > 0, 0).sum()

    # Sum negative removals (since typically Removals are negative)
    gross_removals = ghg_df["Removals"].where(ghg_df["Removals"] < 0, 0).sum()

    # Net flux = sum of gross emissions + gross removals
    net_flux = gross_emissions + gross_removals
    return (gross_emissions, gross_removals, net_flux)


def build_inventory_rows(ghg_df: pd.DataFrame, feature_id: str, year_range: str) -> pd.DataFrame:
    """
    Build the entire GHG inventory for this feature & year range, but
    combine Emissions + Removals into a single column "GHG flux (t CO2 / year)".

    Final columns:
      - FeatureID
      - YearRange
      - Category
      - Type
      - Emissions/Removals
      - Area (ha, total)
      - GHG flux (t CO2 / year)

    We assume 'split_emissions_removals' has been called, so "Emissions" and "Removals" exist.
    """
    # Ensure necessary columns exist
    needed_cols = ["Category", "Type", "Emissions/Removals", "Area (ha, total)", "Emissions", "Removals"]
    for col in needed_cols:
        if col not in ghg_df.columns:
            ghg_df[col] = 0  # or blank

    # Create a copy with only the needed columns
    inventory_df = ghg_df[needed_cols].copy()

    # Combine Emissions + Removals into one column
    # Emissions are typically >=0, Removals <=0, so the sum is that row's net flux
    inventory_df["GHG flux (t CO2 / year)"] = inventory_df["Emissions"] + inventory_df["Removals"]

    # Insert FeatureID, YearRange
    inventory_df["FeatureID"] = feature_id
    inventory_df["YearRange"] = year_range

    # Drop the separate Emissions, Removals columns
    inventory_df.drop(["Emissions", "Removals"], axis=1, inplace=True)

    # Reorder for readability
    columns_order = [
        "FeatureID",
        "YearRange",
        "Category",
        "Type",
        "Emissions/Removals",
        "Area (ha, total)",
        "GHG flux (t CO2 / year)"
    ]
    inventory_df = inventory_df[columns_order]

    return inventory_df


def process_feature(
        feature_id,
        geometry,
        year1,
        year2,
        tree_canopy_source,
        output_base_folder
) -> dict:
    """
    Process one feature geometry: run the communities analysis,
    produce landuse_result, forest_type_result, summary CSV for that feature,
    then return both single-row gross/net flux AND the multi-row GHG inventory.

    Return:
      {
        "flux_row": {FeatureID, YearRange, GrossEmissions, ...},
        "inventory_rows": <DataFrame of full GHG inventory with 1 row per category/type>
      }
      or empty dict on error.
    """
    try:
        # Copy geometry
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{feature_id}")

        # Prepare config
        input_config = get_input_config(str(year1), str(year2), aoi_name=None, tree_canopy_source=tree_canopy_source)
        input_config["cell_size"] = CELL_SIZE
        input_config["year1"] = year1
        input_config["year2"] = year2
        input_config["aoi"] = aoi_temp

        # Output folder for this feature
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        feature_folder = os.path.join(
            output_base_folder,
            f"{feature_id}_{year1}_{year2}_{timestamp}"
        )
        os.makedirs(feature_folder, exist_ok=True)

        # Run analysis
        landuse_result, forest_type_result = perform_analysis(
            input_config,
            CELL_SIZE,
            year1,
            year2,
            analysis_type='community',
            tree_canopy_source=tree_canopy_source
        )
        if landuse_result is None or forest_type_result is None:
            arcpy.AddWarning(f"Analysis returned None for feature={feature_id}.")
            arcpy.management.Delete(aoi_temp)
            return {}

        # Save raw CSVs
        landuse_csv = os.path.join(feature_folder, f"landuse_result_{timestamp}.csv")
        forest_csv = os.path.join(feature_folder, f"forest_type_result_{timestamp}.csv")
        landuse_result.to_csv(landuse_csv, index=False)
        forest_type_result.to_csv(forest_csv, index=False)

        # Summaries
        landuse_matrix = create_land_cover_transition_matrix(landuse_result)
        tree_canopy_df = summarize_tree_canopy(landuse_result)

        years_diff = year2 - year1
        ghg_df = summarize_ghg(
            landuse_df=landuse_result,
            forest_type_df=forest_type_result,
            years=years_diff,
            emissions_factor=input_config["emissions_factor"],
            removals_factor=input_config["removals_factor"],
            c_to_co2=input_config.get("c_to_co2", 44 / 12),
        )
        # Split single "Emissions/Removals" into numeric columns "Emissions" & "Removals"
        ghg_df = split_emissions_removals(ghg_df)

        # IPCC summary
        ipcc_df = create_ipcc_summary(ghg_df)

        # Write 4-table summary
        summary_csv = os.path.join(feature_folder, f"summary_{timestamp}.csv")
        write_enhanced_summary_csv(
            summary_csv,
            landuse_matrix,
            tree_canopy_df,
            ghg_df,
            ipcc_df,
            str(year1),
            str(year2)
        )

        # Single-row flux result
        gross_emissions, gross_removals, net_flux = calculate_gross_net_flux(ghg_df)
        flux_dict = {
            "FeatureID": feature_id,
            "YearRange": f"{year1}-{year2}",
            "GrossEmissions": round(gross_emissions, 2),
            "GrossRemovals": round(gross_removals, 2),
            "NetFlux": round(net_flux, 2),
        }

        # Multi-row inventory
        inventory_df = build_inventory_rows(ghg_df, feature_id, f"{year1}-{year2}")

        # Cleanup
        arcpy.management.Delete(aoi_temp)

        return {
            "flux_row": flux_dict,
            "inventory_rows": inventory_df,
        }

    except Exception as e:
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


def run_batch_for_scale(
        shapefile: str,
        id_field: str,
        inventory_periods: list,
        tree_canopy_source: str,
        scale_name: str,
        date_str: str
):
    """
    For the given shapefile (scale):
      - For each inventory period, iterate over each feature
      - Write full communities_analysis outputs (landuse/forest + 4-table summary) in a subfolder
      - Collect a single 'flux_row' for each feature+period, plus the full inventory rows
    Then produce two "master" CSVs at the scale level:
      - master_flux_{scale_name}.csv  => one row per feature+period (gross/net flux)
      - master_inventory_{scale_name}.csv => multi-rows per feature+period, with the GHG flux in a single column
    """
    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)

    all_flux_rows = []
    all_inventory_rows = []

    for (year1, year2) in inventory_periods:
        arcpy.AddMessage(f"Processing scale='{scale_name}', period={year1}-{year2}")
        if str(year1) not in VALID_YEARS or str(year2) not in VALID_YEARS:
            arcpy.AddWarning(f"Invalid years: {year1}-{year2}, skipping.")
            continue

        with arcpy.da.SearchCursor(shapefile, [id_field, "SHAPE@"]) as cursor:
            for idx, row in enumerate(cursor):
                feature_id = row[0]
                geometry = row[1]
                arcpy.AddMessage(f"  -> FeatureID={feature_id}, {year1}-{year2}")

                result_data = process_feature(
                    feature_id=feature_id,
                    geometry=geometry,
                    year1=year1,
                    year2=year2,
                    tree_canopy_source=tree_canopy_source,
                    output_base_folder=scale_folder
                )
                if not result_data:
                    continue  # skip on error

                # Extract the single flux row and the multi-row inventory
                flux_dict = result_data["flux_row"]
                inventory_df = result_data["inventory_rows"]

                all_flux_rows.append(flux_dict)
                all_inventory_rows.append(inventory_df)

    # After all features/periods, build master CSVs
    if all_flux_rows:
        df_flux = pd.DataFrame(all_flux_rows)
        master_flux_csv = os.path.join(scale_folder, f"master_flux_{scale_name}.csv")
        df_flux.to_csv(master_flux_csv, index=False)
        arcpy.AddMessage(f"    => Wrote flux summary for scale='{scale_name}' -> {master_flux_csv}")
    else:
        arcpy.AddWarning(f"No flux rows found for scale='{scale_name}'.")

    if all_inventory_rows:
        df_inventory = pd.concat(all_inventory_rows, ignore_index=True)
        master_inv_csv = os.path.join(scale_folder, f"master_inventory_{scale_name}.csv")
        df_inventory.to_csv(master_inv_csv, index=False)
        arcpy.AddMessage(f"    => Wrote full inventory for scale='{scale_name}' -> {master_inv_csv}")
    else:
        arcpy.AddWarning(f"No inventory rows found for scale='{scale_name}'.")


def main():
    """
    Batch script for communities_analysis that:
      - For each scale & inventory period & feature, produces
        landuse/forest CSV + 4-table summary in a subfolder
      - Collects two "master" CSVs at the scale level:
        * master_flux_{scale_name}.csv => one row per feature+period (gross/net flux)
        * master_inventory_{scale_name}.csv => multiple rows per feature+period
          with 'GHG flux (t CO2 / year)' instead of separate Emissions/Removals
    """

    # 1) Inventory Periods
    inventory_periods = [
        (2013, 2016),
        (2016, 2019),
    ]

    # 2) Scales Info
    scales_info = [
        {
            "scale_name": "az_counties",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_test\az_test.shp",
            "id_field": "NAME",
            "tree_canopy_source": "NLCD"
        },
        {
            "scale_name": "az_cities",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_test\az_test.shp",
            "id_field": "NAME",
            "tree_canopy_source": "NLCD"
        }
        # Add more as needed...
    ]

    start_time = dt.now()
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")

    for s_info in scales_info:
        scale_name = s_info["scale_name"]
        shapefile = s_info["shapefile"]
        id_field = s_info["id_field"]
        tree_canopy_source = s_info["tree_canopy_source"]

        arcpy.AddMessage(f"\n=== Running communities batch for scale='{scale_name}' ===")
        run_batch_for_scale(
            shapefile=shapefile,
            id_field=id_field,
            inventory_periods=inventory_periods,
            tree_canopy_source=tree_canopy_source,
            scale_name=scale_name,
            date_str=date_str
        )

    arcpy.AddMessage(f"\nAll scales complete. Total processing time: {dt.now() - start_time}")


if __name__ == "__main__":
    main()
