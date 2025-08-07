import os
import re
import arcpy
import pandas as pd
import gc
import time
import psutil
from datetime import datetime as dt
from config_local import VALID_YEARS, CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import summarize_ghg
from communities_analysis import split_emissions_removals, subtract_recat_from_forest_to_grass

# --- Helper Functions ---

def get_removal_factor_by_state(aoi_fc, state_fc):
    out_intersect = "memory\\aoi_state_intersect"
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


def get_emission_factor_by_nearest_place(aoi_fc, place_fc):
    centroid_fc = "memory\\aoi_centroid"
    arcpy.management.FeatureToPoint(aoi_fc, centroid_fc, "CENTROID")

    out_join = "memory\\centroid_join"
    arcpy.analysis.SpatialJoin(
        centroid_fc, place_fc, out_join, "JOIN_ONE_TO_ONE", "KEEP_ALL", match_option="CLOSEST"
    )

    fields = ["tof_ef", "NAME"]
    row = next(arcpy.da.SearchCursor(out_join, fields), None)
    arcpy.management.Delete(centroid_fc)
    arcpy.management.Delete(out_join)

    return (row[0], row[1]) if row else (95.0, "UnknownPlace")


def calculate_gross_net_flux(ghg_df):
    gross_emissions = ghg_df.loc[ghg_df["Emissions/Removals"] == "Emissions", "GHG Flux (t CO2e/year)"].sum()
    gross_removals = ghg_df.loc[ghg_df["Emissions/Removals"] != "Emissions", "GHG Flux (t CO2e/year)"].sum()
    return gross_emissions, gross_removals, gross_emissions + gross_removals


def process_feature(feature_id, geometry, year1, year2, tree_canopy_source, scratch_gdb, recategorize_mode=False):
    try:
        safe_id = re.sub(r"[^0-9a-zA-Z_]+", "_", str(feature_id))
        temp_fc = os.path.join(scratch_gdb, f"aoi_temp_{safe_id}")
        arcpy.management.CopyFeatures(geometry, temp_fc)

        input_config = get_input_config(str(year1), str(year2), None, tree_canopy_source)
        input_config.update({"cell_size": CELL_SIZE, "year1": year1, "year2": year2, "aoi": temp_fc})

        rem_factor, state = get_removal_factor_by_state(temp_fc, r"C:\GIS\LEARN\TOF\usca_factors\tof_rf_states.shp")
        em_factor, place = get_emission_factor_by_nearest_place(temp_fc, r"C:\GIS\LEARN\TOF\usca_factors\tof_ef_places_states.shp")
        input_config["removals_factor"] = rem_factor
        input_config["emissions_factor"] = em_factor

        arcpy.AddMessage(
            f"Feature '{feature_id}' => TOF RemFactor={rem_factor} from {state}, TOF EmFactor={em_factor} from {place}."
        )

        landuse_result, forest_type_result = perform_analysis(
            input_config, CELL_SIZE, year1, year2, 'community', tree_canopy_source, recategorize_mode
        )

        if (landuse_result is None or landuse_result.empty or
            forest_type_result is None or forest_type_result.empty):
            arcpy.AddWarning(f"Analysis returned empty results for feature={feature_id}.")
            arcpy.management.Delete(temp_fc)
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

        del landuse_result, forest_type_result, ghg_df
        arcpy.management.Delete(temp_fc)
        gc.collect()

        return flux_dict

    except Exception as e:
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


def log_memory(stage):
    mem_MB = psutil.Process(os.getpid()).memory_info().rss / (1024**2)
    arcpy.AddMessage(f"[{stage}] Memory Usage: {mem_MB:.2f} MB")


def is_feature_processed(output_csv, feature_id):
    if not os.path.exists(output_csv):
        return False
    df = pd.read_csv(output_csv, usecols=['FeatureID'])
    return str(feature_id) in df['FeatureID'].astype(str).values

# --- Main Batch Function ---
def run_batch_for_scale(shapefile, id_field, inventory_periods, tree_canopy_source, scale_name, date_str,
                        recategorize_mode=False, chunk_size=100, region_field="STATEFP"):

    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)

    scratch_folder = r"C:\GIS\scratch"
    scratch_gdb = os.path.join(scratch_folder, "scratch.gdb")
    os.makedirs(scratch_folder, exist_ok=True)

    if not arcpy.Exists(scratch_gdb):
        arcpy.management.CreateFileGDB(scratch_folder, "scratch.gdb")

    arcpy.env.workspace = scratch_gdb
    arcpy.env.scratchWorkspace = scratch_gdb
    arcpy.env.overwriteOutput = True

    arcpy.AddMessage("Checking and repairing geometries...")
    arcpy.management.RepairGeometry(shapefile)

    grouped_features = {}
    with arcpy.da.SearchCursor(shapefile, [id_field, region_field, "SHAPE@"]) as cursor:
        for fid, region_val, geom in cursor:
            grouped_features.setdefault(region_val, []).append((fid, geom))

    for year1, year2 in inventory_periods:
        for region_val, feature_list in grouped_features.items():
            region_str = region_val or "UnknownRegion"
            master_flux_csv = os.path.join(scale_folder, f"master_flux_{scale_name}_{region_str}_{year1}_{year2}.csv")

            for start in range(0, len(feature_list), chunk_size):
                end = min(start + chunk_size, len(feature_list))
                log_memory(f"Chunk {start+1}-{end} START")

                flux_rows = []
                for feature_id, geometry in feature_list[start:end]:
                    if is_feature_processed(master_flux_csv, feature_id):
                        arcpy.AddMessage(f"Feature {feature_id} already processed, skipping.")
                        continue
                    result = process_feature(feature_id, geometry, year1, year2, tree_canopy_source, scratch_gdb, recategorize_mode)
                    if result:
                        flux_rows.append(result)
                    arcpy.ClearWorkspaceCache_management()
                    gc.collect()

                if flux_rows:
                    pd.DataFrame(flux_rows).to_csv(master_flux_csv, mode='a', header=not os.path.exists(master_flux_csv), index=False)
                    arcpy.AddMessage(f"Wrote chunk {start+1}-{end} to {master_flux_csv}")

                log_memory(f"Chunk {start+1}-{end} END")
                time.sleep(0.5)

# --- Main Execution ---
def main():
    inventory_periods = [(2011, 2013),
                         (2013, 2016),
                         (2016, 2019),
                         (2019, 2021),
                         (2021, 2023)]
    shapefile = r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_place\tl_2023_us_place_conus.shp"
    date_str = "2025_04_23_03_29"
    run_batch_for_scale(shapefile, "GEOID", inventory_periods, "NLCD", "us_places", date_str)

if __name__ == "__main__":
    main()
