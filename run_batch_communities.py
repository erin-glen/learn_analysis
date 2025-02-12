import os
import sys
import logging
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
# LOGGER SETUP
#####################

def setup_logger(log_file_path=None, level=logging.INFO):
    """
    Configure a logger that logs to console and optionally to a file.
    """
    logger = logging.getLogger("CommunityAnalysisLogger")
    logger.setLevel(level)

    # Avoid adding multiple handlers if this is called multiple times
    if not logger.handlers:
        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch_formatter = logging.Formatter("[%(levelname)s] %(message)s")
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)

        # If we want a file log
        if log_file_path:
            fh = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
            fh.setLevel(level)
            file_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            fh.setFormatter(file_formatter)
            logger.addHandler(fh)

    return logger


#####################
# HELPER FUNCTIONS  #
#####################

logger = logging.getLogger("CommunityAnalysisLogger")  # same named logger

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


def calculate_gross_net_flux(ghg_df: pd.DataFrame) -> tuple:
    """
    Calculate gross emissions, gross removals, and net flux (t CO2e/yr).
    Relies on ghg_df["Emissions/Removals"] to categorize fluxes as
    emissions vs removals, and ghg_df["GHG Flux (t CO2e/year)"] for flux amounts.

    Returns:
        (gross_emissions, gross_removals, net_flux), or None if required columns are missing.
    """
    required_cols = ["GHG Flux (t CO2e/year)", "Emissions/Removals"]
    for col in required_cols:
        if col not in ghg_df.columns:
            logger.error(f"calculate_gross_net_flux: missing required column '{col}'.")
            return None

    gross_emissions = 0.0
    gross_removals = 0.0

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
    Build the entire GHG inventory for this feature & year range, combining
    Emissions + Removals into one "GHG flux (t CO2 / year)" column.
    """
    needed_cols = ["Category", "Type", "Emissions/Removals", "Area (ha, total)", "Emissions", "Removals"]
    for col in needed_cols:
        if col not in ghg_df.columns:
            ghg_df[col] = 0  # fallback

    inventory_df = ghg_df[needed_cols].copy()
    inventory_df["GHG flux (t CO2 / year)"] = inventory_df["Emissions"] + inventory_df["Removals"]

    inventory_df["FeatureID"] = feature_id
    inventory_df["YearRange"] = year_range

    # Remove Emissions/Removals columns to avoid confusion; we already combined them
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
        recategorize_mode=False
) -> dict:
    try:
        safe_feature_id = feature_id.replace(" ", "_")
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{safe_feature_id}")

        input_config = get_input_config(
            str(year1), str(year2),
            aoi_fc=aoi_temp,
            aoi_name=feature_id,
            tree_canopy_source=tree_canopy_source
        )
        input_config["cell_size"] = CELL_SIZE
        input_config["year1"] = year1
        input_config["year2"] = year2
        input_config["aoi"] = aoi_temp

        # Factor lookups if missing
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

        logger.info(
            f"Feature '{feature_id}' => TOF RemFactor={input_config['removals_factor']} from {used_state}, "
            f"TOF EmFactor={input_config['emissions_factor']} from {used_place}."
        )
        arcpy.AddMessage(
            f"Feature '{feature_id}' => TOF RemFactor={input_config['removals_factor']} from {used_state}, "
            f"TOF EmFactor={input_config['emissions_factor']} from {used_place}."
        )

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
            logger.warning(f"Analysis returned None for feature={feature_id}. Check logs for errors.")
            arcpy.management.Delete(aoi_temp)
            return {}

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        feature_folder = os.path.join(output_base_folder, f"{safe_feature_id}_{year1}_{year2}_{timestamp}")
        os.makedirs(feature_folder, exist_ok=True)

        landuse_csv = os.path.join(feature_folder, f"landuse_result_{timestamp}.csv")
        forest_csv = os.path.join(feature_folder, f"forest_type_result_{timestamp}.csv")
        landuse_result.to_csv(landuse_csv, index=False)
        forest_type_result.to_csv(forest_csv, index=False)

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
            c_to_co2=input_config.get("c_to_co2", 44/12),
        )

        # **NEW** check for None or empty
        if ghg_df is None:
            logger.error(f"summarize_ghg returned None for feature={feature_id}; cannot proceed.")
            arcpy.AddError(f"summarize_ghg returned None for feature={feature_id}. See logs.")
            arcpy.management.Delete(aoi_temp)
            return {}
        if ghg_df.empty:
            logger.warning(f"summarize_ghg returned an empty DataFrame for feature={feature_id}.")

        from communities_analysis import split_emissions_removals, create_ipcc_summary, write_enhanced_summary_csv
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

        # Build flux row and inventory
        from run_batch_communities import calculate_gross_net_flux, build_inventory_rows
        gross_result = calculate_gross_net_flux(ghg_df)
        if gross_result is None:
            logger.error(f"calculate_gross_net_flux returned None for feature={feature_id}; missing columns?")
            arcpy.AddError(f"calculate_gross_net_flux returned None for feature={feature_id}. See logs.")
            arcpy.management.Delete(aoi_temp)
            return {}

        gross_emissions, gross_removals, net_flux = gross_result
        flux_dict = {
            "FeatureID": feature_id,
            "YearRange": f"{year1}-{year2}",
            "GrossEmissions": round(gross_emissions, 2),
            "GrossRemovals": round(gross_removals, 2),
            "NetFlux": round(net_flux, 2),
        }
        inventory_df = build_inventory_rows(ghg_df, feature_id, f"{year1}-{year2}")

        arcpy.management.Delete(aoi_temp)
        return {"flux_row": flux_dict, "inventory_rows": inventory_df}

    except Exception as e:
        logger.error(f"Error processing feature={feature_id}: {e}", exc_info=True)
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


def run_batch_for_scale(
    shapefile: str,
    id_field: str,
    inventory_periods: list,
    tree_canopy_source: str,
    scale_name: str,
    date_str: str,
    recategorize_mode=False
):
    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)

    all_flux_rows = []
    all_inventory_rows = []

    for (year1, year2) in inventory_periods:
        logger.info(f"Processing scale='{scale_name}', period={year1}-{year2}")
        arcpy.AddMessage(f"Processing scale='{scale_name}', period={year1}-{year2}")
        if str(year1) not in VALID_YEARS or str(year2) not in VALID_YEARS:
            logger.warning(f"Invalid years: {year1}-{year2}, skipping.")
            arcpy.AddWarning(f"Invalid years: {year1}-{year2}, skipping.")
            continue

        with arcpy.da.SearchCursor(shapefile, [id_field, "SHAPE@"]) as cursor:
            for idx, row in enumerate(cursor):
                feature_id = row[0]
                geometry = row[1]
                logger.info(f"  -> FeatureID={feature_id}, {year1}-{year2}")
                arcpy.AddMessage(f"  -> FeatureID={feature_id}, {year1}-{year2}")

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

    if all_flux_rows:
        df_flux = pd.DataFrame(all_flux_rows)
        master_flux_csv = os.path.join(scale_folder, f"master_flux_{scale_name}.csv")
        df_flux.to_csv(master_flux_csv, index=False)
        logger.info(f"Wrote flux summary for scale='{scale_name}' -> {master_flux_csv}")
        arcpy.AddMessage(f"  => Wrote flux summary for scale='{scale_name}' -> {master_flux_csv}")
    else:
        logger.warning(f"No flux rows found for scale='{scale_name}'.")
        arcpy.AddWarning(f"No flux rows found for scale='{scale_name}'.")

    if all_inventory_rows:
        df_inventory = pd.concat(all_inventory_rows, ignore_index=True)
        master_inv_csv = os.path.join(scale_folder, f"master_inventory_{scale_name}.csv")
        df_inventory.to_csv(master_inv_csv, index=False)
        logger.info(f"Wrote full inventory for scale='{scale_name}' -> {master_inv_csv}")
        arcpy.AddMessage(f"  => Wrote full inventory for scale='{scale_name}' -> {master_inv_csv}")
    else:
        logger.warning(f"No inventory rows found for scale='{scale_name}'.")
        arcpy.AddWarning(f"No inventory rows found for scale='{scale_name}'.")


def main():
    # Set up a log file in the output base dir
    log_file = os.path.join(OUTPUT_BASE_DIR, "community_analysis.log")
    setup_logger(log_file, level=logging.INFO)

    logger.info("Starting run_batch_communities main()")
    start_time = dt.now()
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")

    # Example inventory periods
    inventory_periods = [(2011,2013),(2013,2016),(2016,2019),(2019,2021),(2021,2023)]

    scales_info = [
        {
            "scale_name": "az_counties",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_counties\shapefiles\az_counties\az_counties.shp",
            "id_field": "NAME",
            "tree_canopy_source": "NLCD"
        },
        {
            "scale_name": "az_state",
            "shapefile": r"C:\GIS\Data\LEARN\census\Arizona\az_state\az_state\az_state.shp",
            "id_field": "NAME",
            "tree_canopy_source": "NLCD"
        }
    ]

    enable_recategorization = False

    for s_info in scales_info:
        scale_name = s_info["scale_name"]
        shapefile = s_info["shapefile"]
        id_field = s_info["id_field"]
        tree_canopy_source = s_info["tree_canopy_source"]

        logger.info(f"=== Running batch for scale='{scale_name}' ===")
        arcpy.AddMessage(f"\n=== Running communities batch for scale='{scale_name}' ===")
        run_batch_for_scale(
            shapefile=shapefile,
            id_field=id_field,
            inventory_periods=inventory_periods,
            tree_canopy_source=tree_canopy_source,
            scale_name=scale_name,
            date_str=date_str,
            recategorize_mode=enable_recategorization
        )

    total_time = dt.now() - start_time
    logger.info(f"All scales complete. Total processing time: {total_time}")
    arcpy.AddMessage(f"\nAll scales complete. Total processing time: {total_time}")


if __name__ == "__main__":
    main()
