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
    that has separate numeric columns "Emissions" and "Removals".
    (We assume you've already called split_emissions_removals on the DataFrame.)
    """
    if "Emissions" not in ghg_df.columns or "Removals" not in ghg_df.columns:
        return (0.0, 0.0, 0.0)

    # Gross emissions = sum of all positive emission values
    gross_emissions = ghg_df["Emissions"].where(ghg_df["Emissions"] > 0, 0).sum()

    # Gross removals = sum of negative removal values (most likely negative)
    # Some workflows keep them negative, so we sum only negative entries
    gross_removals = ghg_df["Removals"].where(ghg_df["Removals"] < 0, 0).sum()

    # Net flux = sum(gross emissions + gross removals)
    net_flux = gross_emissions + gross_removals
    return (gross_emissions, gross_removals, net_flux)


def process_feature(
    feature_id,
    geometry,
    year1,
    year2,
    tree_canopy_source,
    output_base_folder
) -> dict:
    """
    Process a single feature geometry:
      - Build input_config
      - run perform_analysis
      - produce the 4-table "enhanced summary" CSV,
        plus landuse_result & forest_type_result CSVs
      - compute gross/net flux

    Returns a dictionary for the "master" summary with gross/net flux, or an empty dict on failure.
    """
    try:
        # Copy geometry to in_memory
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{feature_id}")

        # Prepare config
        input_config = get_input_config(str(year1), str(year2), aoi_name=None, tree_canopy_source=tree_canopy_source)
        input_config["cell_size"] = CELL_SIZE
        input_config["year1"] = year1
        input_config["year2"] = year2
        input_config["aoi"] = aoi_temp

        # Output folder for this feature (something like "FeatureID_2013_2016_<timestamp>")
        # or you might just store them all in a single folder for the entire shapefile.
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

        # Write raw CSVs (landuse, forest_type)
        landuse_csv = os.path.join(feature_folder, f"landuse_result_{timestamp}.csv")
        forest_csv = os.path.join(feature_folder, f"forest_type_result_{timestamp}.csv")
        landuse_result.to_csv(landuse_csv, index=False)
        forest_type_result.to_csv(forest_csv, index=False)

        # Summaries for "enhanced" 4-table CSV
        landuse_matrix = create_land_cover_transition_matrix(landuse_result)
        tree_canopy_df = summarize_tree_canopy(landuse_result)

        # Summarize GHG
        years_diff = year2 - year1
        ghg_df = summarize_ghg(
            landuse_df=landuse_result,
            forest_type_df=forest_type_result,
            years=years_diff,
            emissions_factor=input_config["emissions_factor"],
            removals_factor=input_config["removals_factor"],
            c_to_co2=input_config.get("c_to_co2", 44/12),
        )
        ghg_df = split_emissions_removals(ghg_df)
        ipcc_df = create_ipcc_summary(ghg_df)

        # Write the 4-table summary CSV
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

        # Calculate gross, net
        gross_emissions, gross_removals, net_flux = calculate_gross_net_flux(ghg_df)

        # Cleanup
        arcpy.management.Delete(aoi_temp)

        return {
            "FeatureID": feature_id,
            "YearRange": f"{year1}-{year2}",
            "GrossEmissions": round(gross_emissions, 2),
            "GrossRemovals": round(gross_removals, 2),
            "NetFlux": round(net_flux, 2)
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
) -> None:
    """
    For the given shapefile (scale), run communities_analysis for each
    feature, each inventory period, saving a full 4-table output each time
    AND building a master CSV with gross/net flux rows.
    """
    # Create a single folder for this scale
    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)

    # We'll accumulate (FeatureID, YearRange, Emissions, Removals, NetFlux) in a list
    master_rows = []

    for (year1, year2) in inventory_periods:
        arcpy.AddMessage(f"Processing scale='{scale_name}', period={year1}-{year2}")
        # Validate
        if str(year1) not in VALID_YEARS or str(year2) not in VALID_YEARS:
            arcpy.AddWarning(f"Invalid years: {year1}-{year2} ... skipping.")
            continue

        with arcpy.da.SearchCursor(shapefile, [id_field, "SHAPE@"]) as cursor:
            for idx, row in enumerate(cursor):
                feature_id = row[0]
                geometry = row[1]
                arcpy.AddMessage(f"  -> FeatureID={feature_id}, {year1}-{year2}")
                result_dict = process_feature(
                    feature_id=feature_id,
                    geometry=geometry,
                    year1=year1,
                    year2=year2,
                    tree_canopy_source=tree_canopy_source,
                    output_base_folder=scale_folder
                )
                if result_dict:
                    master_rows.append(result_dict)

    # After all features & periods, create a "master" CSV with gross/net flux
    if master_rows:
        df_master = pd.DataFrame(master_rows)
        master_csv = os.path.join(scale_folder, f"master_{scale_name}.csv")
        df_master.to_csv(master_csv, index=False)
        arcpy.AddMessage(f"Wrote master CSV for scale='{scale_name}': {master_csv}")
    else:
        arcpy.AddWarning(f"No results at all for scale='{scale_name}'.")


def main():
    """
    Example batch script that:
      - Takes multiple scales (shapefiles + ID field + tree canopy).
      - Takes multiple inventory periods.
      - For each scale & period & feature, runs the full communities workflow
        and produces landuse_result, forest_type_result, summary_4table CSVs.
      - Meanwhile, collects a single row of "GrossEmissions/GrossRemovals/NetFlux"
        from each run in a scale-level "master" CSV.
    """

    # 1) Inventory Periods
    inventory_periods = [
        (2013, 2016),
        (2016, 2019),
        (2019, 2021),
    ]

    # 2) Scales Info
    scales_info = [
        {
            "scale_name": "az_counties",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_counties.shp",
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

        arcpy.AddMessage(f"\n=== Running batch for scale='{scale_name}' ===")
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
