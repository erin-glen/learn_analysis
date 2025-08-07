import os
import re
import arcpy
import pandas as pd
import gc
from datetime import datetime as dt
from config_local import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import summarize_ghg
from communities_analysis import split_emissions_removals, subtract_recat_from_forest_to_grass

# (Helper functions retained exactly as previously defined)
def get_removal_factor_by_state(aoi_fc: str, state_fc: str) -> tuple:
    out_intersect = "in_memory\\aoi_state_intersect"
    arcpy.analysis.Intersect([aoi_fc, state_fc], out_intersect)

    best_factor, best_state_name, largest_area = None, None, 0
    fields = ["tof_rf", "NAME", "SHAPE@AREA"]
    with arcpy.da.SearchCursor(out_intersect, fields) as cur:
        for row in cur:
            if row[2] > largest_area:
                largest_area = row[2]
                best_factor, best_state_name = row[0], row[1]

    arcpy.management.Delete(out_intersect)
    return best_factor or -3.0, best_state_name or "UnknownState"


def get_emission_factor_by_nearest_place(aoi_fc: str, place_fc: str) -> tuple:
    centroid_fc = "in_memory\\aoi_centroid"
    arcpy.management.FeatureToPoint(aoi_fc, centroid_fc, "CENTROID")

    out_join = "in_memory\\centroid_join"
    arcpy.analysis.SpatialJoin(
        centroid_fc, place_fc, out_join, "JOIN_ONE_TO_ONE", "KEEP_ALL", match_option="CLOSEST"
    )

    fields = ["tof_ef", "NAME"]
    row = next(arcpy.da.SearchCursor(out_join, fields), None)
    arcpy.management.Delete(centroid_fc)
    arcpy.management.Delete(out_join)

    return (row[0], row[1]) if row else (95.0, "UnknownPlace")


def calculate_gross_net_flux(ghg_df: pd.DataFrame) -> tuple:
    gross_emissions = ghg_df.loc[ghg_df["Emissions/Removals"] == "Emissions", "GHG Flux (t CO2e/year)"].sum()
    gross_removals = ghg_df.loc[ghg_df["Emissions/Removals"] != "Emissions", "GHG Flux (t CO2e/year)"].sum()
    return gross_emissions, gross_removals, gross_emissions + gross_removals


# Process individual feature
def process_feature(
    feature_id,
    geometry,
    year1,
    year2,
    tree_canopy_source,
    recategorize_mode=False
):
    try:
        safe_id = re.sub(r"[^0-9a-zA-Z_]+", "_", str(feature_id))
        aoi_temp = arcpy.management.CopyFeatures(geometry, f"in_memory\\aoi_temp_{safe_id}")

        input_config = get_input_config(str(year1), str(year2), None, tree_canopy_source)
        input_config.update({"cell_size": CELL_SIZE, "year1": year1, "year2": year2, "aoi": aoi_temp})

        rem_factor, state = get_removal_factor_by_state(aoi_temp, r"C:\GIS\LEARN\TOF\usca_factors\tof_rf_states.shp")
        em_factor, place = get_emission_factor_by_nearest_place(aoi_temp, r"C:\GIS\LEARN\TOF\usca_factors\tof_ef_places_states.shp")
        input_config.setdefault("removals_factor", rem_factor)
        input_config.setdefault("emissions_factor", em_factor)

        arcpy.AddMessage(
            f"Feature '{feature_id}' => TOF RemFactor={rem_factor} from {state}, TOF EmFactor={em_factor} from {place}."
        )

        landuse_result, forest_type_result = perform_analysis(
            input_config, CELL_SIZE, year1, year2, 'community', tree_canopy_source, recategorize_mode
        )

        if (landuse_result is None or landuse_result.empty or
            forest_type_result is None or forest_type_result.empty):
            arcpy.AddWarning(f"Analysis returned empty results for feature={feature_id}.")
            arcpy.management.Delete(aoi_temp)
            return {}

        ghg_df = summarize_ghg(
            landuse_result, forest_type_result, year2 - year1,
            em_factor, rem_factor, input_config.get("c_to_co2", 44/12),
        )
        ghg_df = split_emissions_removals(ghg_df)
        if recategorize_mode:
            ghg_df = subtract_recat_from_forest_to_grass(ghg_df, forest_type_result)

        gross_emissions, gross_removals, net_flux = calculate_gross_net_flux(ghg_df)
        flux_dict = {
            "FeatureID": feature_id,
            "YearRange": f"{year1}-{year2}",
            "GrossEmissions": round(gross_emissions, 2),
            "GrossRemovals": round(gross_removals, 2),
            "NetFlux": round(net_flux, 2),
        }

        arcpy.management.Delete(aoi_temp)
        del landuse_result, forest_type_result, ghg_df
        gc.collect()

        return flux_dict

    except Exception as e:
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


# Chunked batch processing by state/region
def run_batch_for_scale(
    shapefile,
    id_field,
    inventory_periods,
    tree_canopy_source,
    scale_name,
    date_str,
    recategorize_mode=False,
    chunk_size=1000,
    region_field="STATEFP"  # Adjust to whatever field you prefer to group on
):
    """
    Splits the shapefile into groups by `region_field`, then processes each group
    in chunks of `chunk_size`. Writes out a CSV per region, or you can adapt to
    write a single CSV for all if desired.
    """
    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)
    scratch_folder = r"C:\GIS\scratch"
    scratch_gdb = os.path.join(scratch_folder, "scratch.gdb")

    # Ensure scratch folder exists explicitly
    os.makedirs(scratch_folder, exist_ok=True)
    if not arcpy.Exists(scratch_gdb):
        arcpy.management.CreateFileGDB(scratch_folder, "scratch.gdb")

    arcpy.env.workspace = scratch_gdb
    arcpy.env.scratchWorkspace = scratch_gdb
    arcpy.env.overwriteOutput = True

    arcpy.AddMessage("Checking and repairing geometries...")
    arcpy.management.RepairGeometry(shapefile)

    # Collect features in memory, grouped by region_field
    grouped_features = {}
    with arcpy.da.SearchCursor(shapefile, [id_field, region_field, "SHAPE@"]) as cursor:
        for fid, region_val, geom in cursor:
            if region_val not in grouped_features:
                grouped_features[region_val] = []
            grouped_features[region_val].append((fid, geom))

    # For each inventory period, process features in each region
    for year1, year2 in inventory_periods:
        if str(year1) not in VALID_YEARS or str(year2) not in VALID_YEARS:
            arcpy.AddWarning(f"Skipping invalid year pair: {year1}-{year2}")
            continue

        for region_val, feature_list in grouped_features.items():
            region_str = str(region_val) if region_val is not None else "UnknownRegion"

            # Create a per-region CSV name (or you can keep a single CSV for all)
            master_flux_csv = os.path.join(
                scale_folder, f"master_flux_{scale_name}_{region_str}_{year1}_{year2}.csv"
            )

            total_features = len(feature_list)
            arcpy.AddMessage(
                f"\nProcessing region='{region_str}' for years {year1}-{year2} "
                f"({total_features} features total)."
            )

            for start in range(0, total_features, chunk_size):
                end = min(start + chunk_size, total_features)
                arcpy.AddMessage(f"  - Chunk {start+1}-{end} of {total_features}")

                flux_rows = []
                for feature_id, geometry in feature_list[start:end]:
                    result = process_feature(
                        feature_id, geometry, year1, year2, tree_canopy_source, recategorize_mode
                    )
                    if result:
                        flux_rows.append(result)

                    arcpy.ClearEnvironment("extent")
                    arcpy.Delete_management("in_memory")
                    gc.collect()

                if flux_rows:
                    # Append (mode='a') if file exists, else create with header
                    pd.DataFrame(flux_rows).to_csv(
                        master_flux_csv,
                        mode='a',
                        header=not os.path.exists(master_flux_csv),
                        index=False
                    )
                    arcpy.AddMessage(f"  - Wrote chunk {start+1}-{end} to {master_flux_csv}")


def main():
    inventory_periods = [(2011, 2013)]
    scale_name = "us_counties"
    shapefile = r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_county\tl_2023_us_county_conus.shp"
    id_field = "GEOID"
    tree_canopy_source = "NLCD"
    date_str = dt.now().strftime("%Y_%m_%d_%H_%M")

    # Adjust region_field here to whichever attribute you want to split on
    run_batch_for_scale(
        shapefile,
        id_field,
        inventory_periods,
        tree_canopy_source,
        scale_name,
        date_str,
        chunk_size=1000,
        region_field="STATEFP"
    )

if __name__ == "__main__":
    main()
