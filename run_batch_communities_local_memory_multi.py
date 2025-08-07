import os
import re
import argparse
import arcpy
import pandas as pd
import gc
import multiprocessing
import psutil
import glob
from datetime import datetime as dt
from config_local import CELL_SIZE, OUTPUT_BASE_DIR, get_input_config
from analysis_core import perform_analysis
from funcs import summarize_ghg
from communities_analysis import split_emissions_removals, subtract_recat_from_forest_to_grass


# Helper Functions

def get_removal_factor_by_state(aoi_fc, state_fc, scratch_gdb):
    out_intersect = os.path.join(scratch_gdb, "aoi_state_intersect")
    arcpy.analysis.Intersect([aoi_fc, state_fc], out_intersect)

    best_factor, best_state_name, largest_area = None, None, 0
    fields = ["tof_rf", "NAME", "SHAPE@AREA"]
    with arcpy.da.SearchCursor(out_intersect, fields) as cur:
        for row in cur:
            if row[2] > largest_area:
                largest_area = row[2]
                best_factor, best_state_name = row[0], row[1]
    del cur

    arcpy.management.Delete(out_intersect)
    return best_factor or -3.0, best_state_name or "UnknownState"


def get_emission_factor_by_nearest_place(aoi_fc, place_fc, scratch_gdb):
    centroid_fc = os.path.join(scratch_gdb, "aoi_centroid")
    arcpy.management.FeatureToPoint(aoi_fc, centroid_fc, "CENTROID")

    out_join = os.path.join(scratch_gdb, "centroid_join")
    arcpy.analysis.SpatialJoin(
        centroid_fc, place_fc, out_join, "JOIN_ONE_TO_ONE", "KEEP_ALL", match_option="CLOSEST"
    )

    fields = ["tof_ef", "NAME"]
    with arcpy.da.SearchCursor(out_join, fields) as cursor:
        row = next(cursor, None)
    del cursor

    arcpy.management.Delete(centroid_fc)
    arcpy.management.Delete(out_join)

    return (row[0], row[1]) if row else (95.0, "UnknownPlace")


def calculate_gross_net_flux(ghg_df):
    gross_emissions = ghg_df.loc[ghg_df["Emissions/Removals"] == "Emissions", "GHG Flux (t CO2e/year)"].sum()
    gross_removals = ghg_df.loc[ghg_df["Emissions/Removals"] != "Emissions", "GHG Flux (t CO2e/year)"].sum()
    return gross_emissions, gross_removals, gross_emissions + gross_removals


def log_memory(stage):
    mem_MB = psutil.Process(os.getpid()).memory_info().rss / (1024 ** 2)
    arcpy.AddMessage(f"[{stage}] Memory Usage: {mem_MB:.2f} MB")


def get_processed_ids_for_period(scale_folder, year1, year2):
    processed_ids = set()
    csv_pattern = os.path.join(scale_folder, f"master_flux_*_{year1}_{year2}_chunk_*.csv")

    for csv_file in glob.glob(csv_pattern):
        df = pd.read_csv(csv_file, usecols=['FeatureID'])
        processed_ids.update(df['FeatureID'].astype(str).unique())

    return processed_ids



# Process a single feature
def process_feature(feature_id, geometry, year1, year2, tree_canopy_source, scratch_gdb, recategorize_mode=False):
    try:
        safe_id = re.sub(r"[^0-9a-zA-Z_]+", "_", str(feature_id))
        temp_fc = os.path.join(scratch_gdb, f"aoi_temp_{safe_id}")
        arcpy.management.CopyFeatures(geometry, temp_fc)

        input_config = get_input_config(str(year1), str(year2), None, tree_canopy_source)
        input_config.update({"cell_size": CELL_SIZE, "year1": year1, "year2": year2, "aoi": temp_fc})

        rem_factor, state = get_removal_factor_by_state(temp_fc, r"C:\GIS\LEARN\TOF\usca_factors\tof_rf_states.shp",
                                                        scratch_gdb)
        em_factor, place = get_emission_factor_by_nearest_place(temp_fc,
                                                                r"C:\GIS\LEARN\TOF\usca_factors\tof_ef_places_states.shp",
                                                                scratch_gdb)
        input_config["removals_factor"] = rem_factor
        input_config["emissions_factor"] = em_factor

        landuse_result, forest_type_result = perform_analysis(
            input_config, CELL_SIZE, year1, year2, 'community', tree_canopy_source, recategorize_mode
        )

        if landuse_result is None or landuse_result.empty or forest_type_result is None or forest_type_result.empty:
            arcpy.management.Delete(temp_fc)
            return {}

        ghg_df = summarize_ghg(
            landuse_result, forest_type_result, year2 - year1,
            em_factor, rem_factor, input_config.get("c_to_co2", 44 / 12),
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

        arcpy.management.Delete(temp_fc)
        arcpy.ClearWorkspaceCache_management()
        del landuse_result, forest_type_result, ghg_df
        gc.collect()

        return flux_dict

    except Exception as e:
        arcpy.AddError(f"Error processing feature={feature_id}: {e}")
        return {}


def worker(args):
    state_fp, feature_list, year1, year2, tree_canopy_source, scale_folder, chunk_size = args

    scratch_folder = os.path.join(r"C:\GIS\scratch", f"scratch_{os.getpid()}")
    scratch_gdb = os.path.join(scratch_folder, f"scratch_{os.getpid()}.gdb")
    os.makedirs(scratch_folder, exist_ok=True)
    if not arcpy.Exists(scratch_gdb):
        arcpy.management.CreateFileGDB(scratch_folder, f"scratch_{os.getpid()}.gdb")

    for start in range(0, len(feature_list), chunk_size):

        # Explicit, clear, and stable filename per chunk
        chunk_id = f"{state_fp}_{year1}_{year2}_chunk_{start}"
        chunk_csv = os.path.join(scale_folder, f"master_flux_{chunk_id}.csv")

        # Track FeatureIDs already present in this chunk so re-runs only
        # process missing features rather than skipping the whole chunk
        existing_ids = set()
        if os.path.exists(chunk_csv):
            try:
                existing_ids = set(
                    pd.read_csv(chunk_csv, usecols=["FeatureID"])["FeatureID"].astype(str)
                )
                arcpy.AddMessage(
                    f"Chunk {chunk_id}: found {len(existing_ids)} existing records"
                )
            except Exception as e:
                arcpy.AddWarning(f"Could not read existing chunk {chunk_id}: {e}")

        flux_rows = []
        for feature_id, geometry in feature_list[start:start + chunk_size]:
            if str(feature_id) in existing_ids:
                continue

            result = process_feature(feature_id, geometry, year1, year2, tree_canopy_source, scratch_gdb)
            if result:
                flux_rows.append(result)

            arcpy.ClearWorkspaceCache_management()
            gc.collect()

        if flux_rows:
            chunk_exists = os.path.exists(chunk_csv)
            pd.DataFrame(flux_rows).to_csv(
                chunk_csv,
                mode='a' if chunk_exists else 'w',
                header=not chunk_exists,
                index=False,
            )

        arcpy.AddMessage(
            f"Chunk {chunk_id}: processed {len(flux_rows)} new features, skipped {len(existing_ids)} existing features"
        )
        log_memory(f"Worker {os.getpid()} processed chunk {start}-{start + chunk_size}")

    arcpy.ClearWorkspaceCache_management()
    gc.collect()

    try:
        arcpy.env.workspace = None
        arcpy.env.scratchWorkspace = None
        gc.collect()
        arcpy.management.Delete(scratch_gdb)
    except Exception as e:
        arcpy.AddWarning(f"Could not delete scratch workspace {scratch_gdb}: {e}")


# Merge CSV outputs
def merge_csv_outputs(scale_folder):
    """Merge all chunk CSVs into a single deduplicated master file."""
    all_csv_files = glob.glob(os.path.join(scale_folder, "master_flux_*.csv"))
    if not all_csv_files:
        print("No chunk CSVs found; skipping merge.")
        return

    df_list = [pd.read_csv(csv_file) for csv_file in all_csv_files]
    master_df = pd.concat(df_list).drop_duplicates(subset=["FeatureID", "YearRange"])

    master_csv_path = os.path.join(scale_folder, "master_flux_final.csv")
    master_df.to_csv(master_csv_path, index=False)

    print(f"Merged final CSV written to: {master_csv_path}")


# Core batch runner shared by CLI and built-in run block
def run_batch(
    shapefile,
    id_field,
    scale_name,
    tree_canopy_source,
    inventory_periods,
    processes,
    chunk_size,
    run_date=None,
):
    date_str = run_date or dt.now().strftime("%Y_%m_%d_%H_%M")
    scale_folder = os.path.join(OUTPUT_BASE_DIR, f"{date_str}_{scale_name}")
    os.makedirs(scale_folder, exist_ok=True)

    pool = multiprocessing.Pool(processes=processes)
    jobs = []

    for year1, year2 in inventory_periods:
        processed_ids = get_processed_ids_for_period(scale_folder, year1, year2)
        if processed_ids:
            arcpy.AddMessage(
                f"Period {year1}-{year2}: found {len(processed_ids)} previously processed records"
            )

        grouped_features = {}
        with arcpy.da.SearchCursor(shapefile, [id_field, "STATEFP", "SHAPE@"]) as cursor:
            for fid, state_fp, geom in cursor:
                if str(fid) not in processed_ids:
                    grouped_features.setdefault(state_fp or "UnknownState", []).append((fid, geom))

        for state_fp, feature_list in grouped_features.items():
            if feature_list:
                job_args = (
                    state_fp,
                    feature_list,
                    year1,
                    year2,
                    tree_canopy_source,
                    scale_folder,
                    chunk_size,
                )
                jobs.append(pool.apply_async(worker, args=(job_args,)))

    for job in jobs:
        job.get()

    pool.close()
    pool.join()

    merge_csv_outputs(scale_folder)


# CLI argument parsing and main entry point
def parse_args():
    parser = argparse.ArgumentParser(description="Batch process communities with optional resume support")
    parser.add_argument("--shapefile", required=True, help="Path to input shapefile")
    parser.add_argument("--id-field", default="GEOID", help="Unique ID field in shapefile")
    parser.add_argument("--scale-name", default="us_places", help="Identifier for output folder")
    parser.add_argument("--tree-canopy-source", default="NLCD", help="Tree canopy source")
    parser.add_argument(
        "--period", action="append", nargs=2, metavar=("YEAR1", "YEAR2"), type=int, required=True,
        help="Inventory period pair, e.g. --period 2021 2023",
    )
    parser.add_argument("--processes", type=int, default=multiprocessing.cpu_count(), help="Worker processes")
    parser.add_argument("--chunk-size", type=int, default=50, help="Features per chunk")
    parser.add_argument("--run-date", help="Custom run date for output folder, e.g. 2025_04_23_03_29")
    return parser.parse_args()


def main():
    args = parse_args()
    inventory_periods = [tuple(p) for p in args.period]
    run_batch(
        shapefile=args.shapefile,
        id_field=args.id_field,
        scale_name=args.scale_name,
        tree_canopy_source=args.tree_canopy_source,
        inventory_periods=inventory_periods,
        processes=args.processes,
        chunk_size=args.chunk_size,
        run_date=args.run_date,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        run_date = dt.now().strftime("%Y_%m_%d_%H_%M")
        run_batch(
            shapefile=r"C:\GIS\LEARN\AOI\crosswalk\tl_2023_us_place\tl_2023_us_place_conus.shp",
            id_field="GEOID",
            scale_name="us_places",
            tree_canopy_source="NLCD",
            inventory_periods=[(2021, 2023)],
            processes=6,
            chunk_size=50,
            run_date="2025_04_28_18_42",
        )