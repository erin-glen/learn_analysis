# run_batch_communities.py

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

#####################
# HELPER FUNCTIONS  #
#####################


def get_removal_factor_by_state(aoi_fc: str, state_fc: str) -> tuple:
    """
    Intersect 'aoi_fc' with 'state_fc' polygons to find the best match
    for the removal factor (tof_rf). If the AOI crosses multiple states,
    pick the one with the largest overlap area.

    Returns:
        (removal_factor, state_name)
    """
    out_intersect = "in_memory\\aoi_state_intersect"
    arcpy.analysis.Intersect([aoi_fc, state_fc], out_intersect)

    best_factor = None
    best_state_name = None
    largest_area = 0

    # Adjust these field names to match your shapefile's schema
    fields = ["tof_rf", "STATE_NAME", "SHAPE@AREA"]
    with arcpy.da.SearchCursor(out_intersect, fields) as cur:
        for row in cur:
            this_factor = row[0]     # from 'tof_rf' field
            this_state = row[1]      # from 'STATE_NAME' field
            this_area = row[2]       # intersection area

            if this_area > largest_area:
                largest_area = this_area
                best_factor = this_factor
                best_state_name = this_state

    # Fallback if nothing found
    if best_factor is None:
        best_factor = -3.0
        best_state_name = "UnknownState"

    arcpy.management.Delete(out_intersect)
    return (best_factor, best_state_name)


def get_emission_factor_by_nearest_place(aoi_fc: str, place_fc: str) -> tuple:
    """
    Find the AOI's centroid, then find the nearest polygon in 'place_fc'
    (which has a 'tof_ef' field). Return (emission_factor, place_name).
    """
    # Create centroid from AOI
    centroid_fc = "in_memory\\aoi_centroid"
    arcpy.management.FeatureToPoint(aoi_fc, centroid_fc, "CENTROID")

    # Spatial join with "CLOSEST" to find the nearest polygon
    out_join = "in_memory\\centroid_join"
    arcpy.analysis.SpatialJoin(
        target_features=centroid_fc,
        join_features=place_fc,
        out_feature_class=out_join,
        join_operation="JOIN_ONE_TO_ONE",
        match_option="CLOSEST"
    )

    best_factor = None
    best_place = None

    # Adjust these field names to match your shapefile's schema
    fields = ["tof_ef", "PLACE_NAME"]
    row = next(arcpy.da.SearchCursor(out_join, fields), None)
    if row:
        best_factor = row[0]
        best_place = row[1]
    else:
        # fallback
        best_factor = 95.0
        best_place = "UnknownPlace"

    arcpy.management.Delete(centroid_fc)
    arcpy.management.Delete(out_join)
    return (best_factor, best_place)


############################
# MAIN BATCH SCRIPT LOGIC  #
############################


def calculate_gross_net_flux(ghg_df: pd.DataFrame) -> tuple:
    """
    Calculate gross emissions, gross removals, and net flux.
    BUT we rely on ghg_df["Emissions/Removals"] to decide
    which sum to place it in, *not* the sign of flux.
    """
    if "Emissions" not in ghg_df.columns or "Removals" not in ghg_df.columns:
        return (0.0, 0.0, 0.0)

    gross_emissions = 0.0
    gross_removals = 0.0

    # We assume there's a "GHG Flux (t CO2e/year)" column with negative or positive values
    for idx, row in ghg_df.iterrows():
        flux = row["GHG Flux (t CO2e/year)"]
        if row["Emissions/Removals"] == "Emissions":
            gross_emissions += flux
        else:
            gross_removals += flux

    net_flux = gross_emissions + gross_removals
    return (gross_emissions, gross_removals, net_flux)


def build_inventory_rows(ghg_df: pd.DataFrame, feature_id: str, year_range: str) -> pd.DataFrame:
    """
    Build the entire GHG inventory for this feature & year range, but
    combine Emissions + Removals into one "GHG flux (t CO2 / year)" column.
    """
    needed_cols = ["Category", "Type", "Emissions/Removals", "Area (ha, total)", "Emissions", "Removals"]
    for col in needed_cols:
        if col not in ghg_df.columns:
            ghg_df[col] = 0  # fallback

    inventory_df = ghg_df[needed_cols].copy()
    inventory_df["GHG flux (t CO2 / year)"] = inventory_df["Emissions"] + inventory_df["Removals"]

    inventory_df["FeatureID"] = feature_id
    inventory_df["YearRange"] = year_range

    inventory_df.drop(["Emissions", "Removals"], axis=1, inplace=True)
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
    output_base_folder,
    recategorize_mode=False  # <-- add a toggle for recategorization
) -> dict:
    """
    Process one feature geometry: run the communities analysis,
    produce landuse_result, forest_type_result, summary CSV for that feature,
    then return both single-row gross/net flux AND the multi-row GHG inventory.
    """
    try:
        # Copy geometry for AOI
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{feature_id}")

        # Build config
        input_config = get_input_config(str(year1), str(year2), aoi_name=None, tree_canopy_source=tree_canopy_source)
        input_config["cell_size"] = CELL_SIZE
        input_config["year1"] = year1
        input_config["year2"] = year2
        input_config["aoi"] = aoi_temp

        # If missing emission/removal factors, pull from shapefiles
        if "removals_factor" not in input_config or input_config["removals_factor"] is None:
            (rem_factor, used_state) = get_removal_factor_by_state(
                aoi_temp, r"C:\GIS\Data\LEARN\SourceData\TOF\state_removal_factors.shp"
            )
            input_config["removals_factor"] = rem_factor
        else:
            used_state = "ConfigProvided"

        if "emissions_factor" not in input_config or input_config["emissions_factor"] is None:
            (em_factor, used_place) = get_emission_factor_by_nearest_place(
                aoi_temp, r"C:\GIS\Data\LEARN\SourceData\TOF\place_emission_factors.shp"
            )
            input_config["emissions_factor"] = em_factor
        else:
            used_place = "ConfigProvided"

        arcpy.AddMessage(
            f"Feature '{feature_id}' => TOF RemFactor={input_config['removals_factor']} from {used_state}, "
            f"TOF EmFactor={input_config['emissions_factor']} from {used_place}."
        )

        # Now call perform_analysis with the chosen recategorize_mode
        landuse_result, forest_type_result = perform_analysis(
            input_config,
            CELL_SIZE,
            year1,
            year2,
            analysis_type='community',
            tree_canopy_source=tree_canopy_source,
            recategorize_mode=recategorize_mode
        )
        if landuse_result is None or forest_type_result is None:
            arcpy.AddWarning(f"Analysis returned None for feature={feature_id}.")
            arcpy.management.Delete(aoi_temp)
            return {}

        # Save raw CSVs
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        feature_folder = os.path.join(
            output_base_folder,
            f"{feature_id}_{year1}_{year2}_{timestamp}"
        )
        os.makedirs(feature_folder, exist_ok=True)

        landuse_csv = os.path.join(feature_folder, f"landuse_result_{timestamp}.csv")
        forest_csv = os.path.join(feature_folder, f"forest_type_result_{timestamp}.csv")
        landuse_result.to_csv(landuse_csv, index=False)
        forest_type_result.to_csv(forest_csv, index=False)

        # Summaries
        from funcs import summarize_ghg, summarize_tree_canopy, create_land_cover_transition_matrix
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
        ghg_df = split_emissions_removals(ghg_df)

        ipcc_df = create_ipcc_summary(ghg_df)
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

        # Single-row flux
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
        return {"flux_row": flux_dict, "inventory_rows": inventory_df}

    except Exception as e:
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


def run_batch_for_scale(
    shapefile: str,
    id_field: str,
    inventory_periods: list,
    tree_canopy_source: str,
    scale_name: str,
    date_str: str,
    recategorize_mode=False  # <-- add a toggle for recategorization at the scale level
):
    """
    For the given shapefile (scale):
      - For each inventory period, iterate over each feature
      - Write full communities_analysis outputs (landuse/forest + 4-table summary) in a subfolder
      - Collect a single 'flux_row' for each feature+period, plus the full inventory rows
    Then produce two "master" CSVs at the scale level:
      - master_flux_{scale_name}.csv
      - master_inventory_{scale_name}.csv
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

                # Now we pass the recategorize_mode to process_feature
                result_data = process_feature(
                    feature_id=feature_id,
                    geometry=geometry,
                    year1=year1,
                    year2=year2,
                    tree_canopy_source=tree_canopy_source,
                    output_base_folder=scale_folder,
                    recategorize_mode=recategorize_mode
                )
                if not result_data:
                    continue

                flux_dict = result_data["flux_row"]
                inventory_df = result_data["inventory_rows"]
                all_flux_rows.append(flux_dict)
                all_inventory_rows.append(inventory_df)

    # Master CSVs
    if all_flux_rows:
        df_flux = pd.DataFrame(all_flux_rows)
        master_flux_csv = os.path.join(scale_folder, f"master_flux_{scale_name}.csv")
        df_flux.to_csv(master_flux_csv, index=False)
        arcpy.AddMessage(f"  => Wrote flux summary for scale='{scale_name}' -> {master_flux_csv}")
    else:
        arcpy.AddWarning(f"No flux rows found for scale='{scale_name}'.")

    if all_inventory_rows:
        df_inventory = pd.concat(all_inventory_rows, ignore_index=True)
        master_inv_csv = os.path.join(scale_folder, f"master_inventory_{scale_name}.csv")
        df_inventory.to_csv(master_inv_csv, index=False)
        arcpy.AddMessage(f"  => Wrote full inventory for scale='{scale_name}' -> {master_inv_csv}")
    else:
        arcpy.AddWarning(f"No inventory rows found for scale='{scale_name}'.")


def main():
    """
    Batch script for communities_analysis that:
      - For each scale & inventory period & feature, sets up emission/removal factors
        from shapefiles if not already provided
      - Runs the normal "perform_analysis" for each feature
      - Summarizes results in master flux/inventory CSVs
    """
    # Example inventory periods
    inventory_periods = [(2011,2013),(2013,2016),(2016,2019),(2019,2021),(2021,2023)]

    scales_info = [
        # Example scales (commented out unless you un-comment them)
        # {
        #     "scale_name": "az_counties",
        #     "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_counties\shapefiles\az_counties\az_counties.shp",
        #     "id_field": "NAME",
        #     "tree_canopy_source": "NLCD"
        # },
        # {
        #     "scale_name": "az_places",
        #     "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_places\shapefiles\az_places\az_places.shp",
        #     "id_field": "NAME",
        #     "tree_canopy_source": "NLCD"
        # },
        # {
        #     "scale_name": "az_tribal_nations",
        #     "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_tribal_nations\shapefiles\az_tribal_nations\az_tribal_nations.shp",
        #     "id_field": "NAME",
        #     "tree_canopy_source": "NLCD"
        # },
        {
            "scale_name": "az_state",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_state\az_state\az_state.shp",
            "id_field": "NAME",
            "tree_canopy_source": "NLCD"
        }
    ]

    # Decide if you want to recategorize "Forest to Grassland" w/ disturbances => "Forest Remaining Forest"
    # This is a global toggle, used for all features in all scales below
    enable_recategorization = True

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
            date_str=date_str,
            recategorize_mode=enable_recategorization  # pass to run_batch_for_scale
        )

    arcpy.AddMessage(f"\nAll scales complete. Total processing time: {dt.now() - start_time}")

if __name__ == "__main__":
    main()
