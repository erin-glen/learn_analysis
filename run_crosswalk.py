# run_crosswalk.py
import os
import re
import arcpy
import pandas as pd
from datetime import datetime as dt

# CHANGED: align with run_batch_communities.py, which uses config_local
from config_local import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config

# Same imports from analysis_core, funcs, communities_analysis
from analysis_core import perform_analysis
from funcs import (
    summarize_ghg,
    summarize_tree_canopy,
    create_land_cover_transition_matrix,
)
from communities_analysis import (
    split_emissions_removals,
    create_ipcc_summary,
    subtract_recat_from_forest_to_grass,
    # We do NOT import/write_enhanced_summary_csv here since
    # we are not writing per-feature summary CSVs
)


def get_removal_factor_by_state(aoi_fc: str, state_fc: str) -> tuple:
    """
    Intersect 'aoi_fc' with 'state_fc' polygons to find the best match
    for the removal factor (tof_rf). If the AOI crosses multiple states,
    pick the one with the largest overlap area.
    Returns: (removal_factor, state_name).
    """
    out_intersect = "in_memory\\aoi_state_intersect"
    arcpy.analysis.Intersect([aoi_fc, state_fc], out_intersect)

    best_factor = None
    best_state_name = None
    largest_area = 0

    fields = ["tof_rf", "NAME", "SHAPE@AREA"]
    with arcpy.da.SearchCursor(out_intersect, fields) as cur:
        for row in cur:
            this_factor = row[0]
            this_state = row[1]
            this_area = row[2]

            if this_area > largest_area:
                largest_area = this_area
                best_factor = this_factor
                best_state_name = this_state

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
    centroid_fc = "in_memory\\aoi_centroid"
    arcpy.management.FeatureToPoint(aoi_fc, centroid_fc, "CENTROID")

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

    fields = ["tof_ef", "NAME"]
    row = next(arcpy.da.SearchCursor(out_join, fields), None)
    if row:
        best_factor = row[0]
        best_place = row[1]
    else:
        best_factor = 95.0
        best_place = "UnknownPlace"

    arcpy.management.Delete(centroid_fc)
    arcpy.management.Delete(out_join)
    return (best_factor, best_place)


def calculate_gross_net_flux(ghg_df: pd.DataFrame) -> tuple:
    """
    Calculate gross emissions, gross removals, and net flux from ghg_df.
    We rely on 'Emissions/Removals' to decide how to classify each row.
    """
    if "Emissions" not in ghg_df.columns or "Removals" not in ghg_df.columns:
        return (0.0, 0.0, 0.0)

    gross_emissions = 0.0
    gross_removals = 0.0

    for _, row in ghg_df.iterrows():
        flux = row.get("GHG Flux (t CO2e/year)", 0)
        if row["Emissions/Removals"] == "Emissions":
            gross_emissions += flux
        else:
            gross_removals += flux

    net_flux = gross_emissions + gross_removals
    return (gross_emissions, gross_removals, net_flux)


def build_inventory_rows(ghg_df: pd.DataFrame, feature_id: str, year_range: str) -> pd.DataFrame:
    """
    Build the entire GHG inventory for this feature & year range.
    We combine Emissions + Removals into a single 'GHG flux' column.
    """
    needed_cols = [
        "Category",
        "Type",
        "Emissions/Removals",
        "Area (ha, total)",
        "Emissions",
        "Removals"
    ]
    for col in needed_cols:
        if col not in ghg_df.columns:
            ghg_df[col] = 0  # fallback if missing

    inventory_df = ghg_df[needed_cols].copy()
    inventory_df["GHG flux (t CO2 / year)"] = (
        inventory_df["Emissions"] + inventory_df["Removals"]
    )

    inventory_df["FeatureID"] = feature_id
    inventory_df["YearRange"] = year_range

    # Drop the separate Emissions/Removals numeric columns
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
    return inventory_df[columns_order]


def process_feature(
    feature_id,
    geometry,
    year1,
    year2,
    tree_canopy_source,
    output_base_folder,
    recategorize_mode=False
) -> dict:
    """
    Process one feature geometry in memory:
      - run the communities analysis
      - replicate the new logic from run_batch_communities.py (including recategorize)
      - return single-row flux + multi-row GHG inventory
      NOTE: we do NOT write per-feature CSVs here.
    """
    try:
        # 1) Sanitize feature_id for in-memory usage
        safe_id = re.sub(r"[^0-9a-zA-Z_]+", "_", str(feature_id))
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{safe_id}")

        # 2) Build config
        input_config = get_input_config(
            str(year1), str(year2),
            aoi_name=None,
            tree_canopy_source=tree_canopy_source
        )
        input_config["cell_size"] = CELL_SIZE
        input_config["year1"] = year1
        input_config["year2"] = year2
        input_config["aoi"] = aoi_temp

        # If missing, fetch from shapefiles used in run_batch_communities.py
        if not input_config.get("removals_factor"):
            (rem_factor, used_state) = get_removal_factor_by_state(
                aoi_temp, r"C:\GIS\LEARN\TOF\usca_factors\tof_rf_states.shp"
            )
            input_config["removals_factor"] = rem_factor
        else:
            used_state = "ConfigProvided"

        if not input_config.get("emissions_factor"):
            (em_factor, used_place) = get_emission_factor_by_nearest_place(
                aoi_temp, r"C:\GIS\LEARN\TOF\usca_factors\tof_ef_places_states.shp"
            )
            input_config["emissions_factor"] = em_factor
        else:
            used_place = "ConfigProvided"

        arcpy.AddMessage(
            f"Feature '{feature_id}' => "
            f"TOF RemFactor={input_config['removals_factor']} from {used_state}, "
            f"TOF EmFactor={input_config['emissions_factor']} from {used_place}."
        )

        # 3) Perform analysis
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

        # 4) Summaries in memory
        #    We replicate the new approach from run_batch_communities.py
        #    but do NOT write them to disk per feature.
        years_diff = year2 - year1

        # Create land cover matrix & tree canopy summary for completeness
        # (though we won't write them per feature)
        landuse_matrix = create_land_cover_transition_matrix(landuse_result)
        tree_canopy_df = summarize_tree_canopy(landuse_result)

        ghg_df = summarize_ghg(
            landuse_df=landuse_result,
            forest_type_df=forest_type_result,
            years=years_diff,
            emissions_factor=input_config["emissions_factor"],
            removals_factor=input_config["removals_factor"],
            c_to_co2=input_config.get("c_to_co2", 44 / 12)
        )
        ghg_df = split_emissions_removals(ghg_df)

        # NEW: recategorize logic from run_batch_communities
        if recategorize_mode:
            ghg_df = subtract_recat_from_forest_to_grass(ghg_df, forest_type_result)

        # Round area/emissions/removals
        for col in ["Area (ha, total)", "Emissions", "Removals"]:
            if col in ghg_df.columns:
                ghg_df[col] = pd.to_numeric(ghg_df[col], errors="coerce").fillna(0)
                ghg_df[col] = ghg_df[col].round(0).astype(int)

        ipcc_df = create_ipcc_summary(ghg_df)

        # 5) Single-row flux for this feature
        gross_emissions, gross_removals, net_flux = calculate_gross_net_flux(ghg_df)
        flux_dict = {
            "FeatureID": feature_id,
            "YearRange": f"{year1}-{year2}",
            "GrossEmissions": round(gross_emissions, 2),
            "GrossRemovals": round(gross_removals, 2),
            "NetFlux": round(net_flux, 2),
        }

        # 6) Multi-row inventory
        inventory_df = build_inventory_rows(ghg_df, feature_id, f"{year1}-{year2}")

        # Cleanup
        arcpy.management.Delete(aoi_temp)

        return {
            "flux_row": flux_dict,
            "inventory_rows": inventory_df
            # We do not return or write the landuse matrix, IPCC df, etc.
            # unless you decide to incorporate them into a consolidated CSV.
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
    date_str: str,
    recategorize_mode=False
):
    """
    For the given shapefile (scale):
      - For each inventory period, iterate over each feature
      - Collect flux and inventory data in memory
      - Produce only two CSVs for the entire scale:
          1) master_flux_{scale_name}.csv
          2) master_inventory_{scale_name}.csv
      (No per-feature CSV outputs.)
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
                    output_base_folder=scale_folder,  # not used for per-feature CSV
                    recategorize_mode=recategorize_mode
                )
                if not result_data:
                    continue

                flux_dict = result_data["flux_row"]
                inventory_df = result_data["inventory_rows"]
                all_flux_rows.append(flux_dict)
                all_inventory_rows.append(inventory_df)

    # 1) Master flux CSV
    if all_flux_rows:
        df_flux = pd.DataFrame(all_flux_rows)
        master_flux_csv = os.path.join(scale_folder, f"master_flux_{scale_name}.csv")
        df_flux.to_csv(master_flux_csv, index=False)
        arcpy.AddMessage(f"  => Wrote flux summary for scale='{scale_name}' -> {master_flux_csv}")
    else:
        arcpy.AddWarning(f"No flux rows found for scale='{scale_name}'.")

    # 2) Master inventory CSV
    if all_inventory_rows:
        df_inventory = pd.concat(all_inventory_rows, ignore_index=True)
        master_inv_csv = os.path.join(scale_folder, f"master_inventory_{scale_name}.csv")
        df_inventory.to_csv(master_inv_csv, index=False)
        arcpy.AddMessage(f"  => Wrote full inventory for scale='{scale_name}' -> {master_inv_csv}")
    else:
        arcpy.AddWarning(f"No inventory rows found for scale='{scale_name}'.")


def main():
    """
    Updated run_crosswalk: aligns with run_batch_communities
    in logic/processing (incl. recategorize_mode),
    but does NOT write per-feature CSVs.
    """
    # Example: same default inventory_periods as run_batch_communities
    # You can adjust or reduce as needed.
    inventory_periods = [
        (2011, 2013)
        # (2013, 2016),
        # (2016, 2019),
        # (2019, 2021),
        # (2021, 2023)
    ]

    # Example: one scale_info entry, but feel free to add more
    scales_info = [
        # {
        #     "scale_name": "us_states",
        #     "shapefile": r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_state\tl_2023_us_state.shp",
        #     "id_field": "STATEFP",
        #     "tree_canopy_source": "NLCD"
        # },
        {
            "scale_name": "us_counties",
            "shapefile": r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_county\tl_2023_us_county.shp",
            "id_field": "GEOID",
            "tree_canopy_source": "NLCD"
        }
        # {
        #     "scale_name": "us_tribal",
        #     "shapefile": r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_aiannh\tl_2023_us_aiannh.shp",
        #     "id_field": "GEOID",
        #     "tree_canopy_source": "NLCD"
        # }
    ]

    enable_recategorization = False
    start_time = dt.now()
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")

    for s_info in scales_info:
        scale_name = s_info["scale_name"]
        shapefile = s_info["shapefile"]
        id_field = s_info["id_field"]
        tree_canopy_source = s_info["tree_canopy_source"]

        arcpy.AddMessage(f"\n=== Running crosswalk batch for scale='{scale_name}' ===")
        run_batch_for_scale(
            shapefile=shapefile,
            id_field=id_field,
            inventory_periods=inventory_periods,
            tree_canopy_source=tree_canopy_source,
            scale_name=scale_name,
            date_str=date_str,
            recategorize_mode=enable_recategorization
        )

    arcpy.AddMessage(f"\nAll scales complete. Total processing time: {dt.now() - start_time}")


if __name__ == "__main__":
    main()
