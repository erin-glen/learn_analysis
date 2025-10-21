"""
Processes raw Insect/Disease data by reading ESRI FileGDB layers
using OGR, filtering by year, and rasterizing each region to match
the NLCD resolution/extent. Must be run in a GDAL/OGR environment.

Example
-------
```
python insect_disease_process.py --period 2019_2021
```
"""

import os
import subprocess
import logging
import sys
import argparse

import rasterio
import disturbance_config as cfg

def main():
    """
    1) Parse --period argument (e.g. '2019_2021').
    2) Convert that period string into a list of years [2019, 2020, 2021].
    3) For each region in cfg.REGIONS, extract features from the GDB for those years.
    4) Rasterize and save output as: insect_damage_{region}_{period}.tif
    """

    # ---------------------------------------------------------
    # 1. Parse Command-Line Argument: e.g. --period 2019_2021
    # ---------------------------------------------------------
    parser = argparse.ArgumentParser(description="Run Insect/Disease processing for a single user-specified period.")
    parser.add_argument(
        "--period",
        required=True,
        help="Time period in the format 'YYYY_YYYY' (e.g. '2019_2021')."
    )
    args = parser.parse_args()

    # Parse the string "YYYY_YYYY" => [YYYY, YYYY+1, ..., YYYY2]
    try:
        start_str, end_str = args.period.split("_")
        start_year = int(start_str)
        end_year = int(end_str)
    except ValueError:
        logging.error(f"Invalid --period '{args.period}'. Use format YYYY_YYYY, e.g. 2019_2021.")
        sys.exit(1)

    if end_year < start_year:
        logging.error(f"Invalid period range: {start_year} > {end_year}")
        sys.exit(1)

    # Build a list of years inclusive of start/end
    years = list(range(start_year, end_year + 1))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.info("Insect/Disease Extraction Script Started.")
    logging.info(f"Requested period: {args.period} => years={years}")

    # ---------------------------------------------------------
    # 2. Load NLCD metadata using rasterio
    # ---------------------------------------------------------
    with rasterio.open(cfg.NLCD_RASTER) as nl:
        nl_bounds = nl.bounds
        nl_res = nl.res
        nl_crs = nl.crs

    logging.info(f"NLCD: bounds={nl_bounds}, res={nl_res}, crs={nl_crs}")

    # ---------------------------------------------------------
    # 3. Region-by-region extraction & rasterization
    # ---------------------------------------------------------
    for region in cfg.REGIONS:
        gdb_folder_name = f"CONUS_Region{region}_AllYears.gdb"
        gdb_path = os.path.join(cfg.INSECT_GDB_DIR, gdb_folder_name, gdb_folder_name)

        if not os.path.exists(gdb_path):
            logging.warning(f"GDB not found for region={region}: {gdb_path}")
            continue

        # Find relevant layer using ogrinfo
        try:
            ogrinfo_cmd = ['ogrinfo', '-ro', '-so', '-al', gdb_path]
            result = subprocess.run(ogrinfo_cmd, capture_output=True, text=True, check=True)
            layer_list = [
                line.strip().split(':')[-1].strip()
                for line in result.stdout.split('\n')
                if 'Layer name:' in line
            ]
        except subprocess.CalledProcessError as e:
            logging.error(f"Error running ogrinfo on {gdb_path}: {e}")
            continue

        layer_name = next(
            (ly for ly in layer_list if 'DAMAGE_AREAS_FLAT' in ly and f"Rgn{region}" in ly),
            None
        )
        if not layer_name:
            logging.warning(f"No matching layer found for region={region}")
            continue

        logging.info(f"Region={region}, using layer={layer_name}")

        # Output raster name will reflect the user-specified period
        output_raster = os.path.join(
            cfg.INSECT_OUTPUT_DIR,
            f"insect_damage_{region}_{args.period}.tif"
        )
        if os.path.exists(output_raster):
            logging.info(f"Already exists: {output_raster}, skipping.")
            continue

        # Build SQL:
        #   SELECT ..., damage_val
        #   WHERE SURVEY_YEAR in (2019,2020,2021,...)
        #   damage_val = 5 if 'Mortality - Previously Undocumented', else 0
        year_str = ",".join(map(str, years))
        sql_query = (
            "SELECT *, CASE WHEN DAMAGE_TYPE = 'Mortality - Previously Undocumented' "
            "THEN 5 ELSE 0 END AS damage_val "
            f"FROM '{layer_name}' WHERE SURVEY_YEAR IN ({year_str})"
        )

        # 3A) Convert relevant features to a temp GPKG
        temp_vector = os.path.join(
            cfg.INSECT_OUTPUT_DIR,
            f"temp_region{region}_{args.period}.gpkg"
        )
        if os.path.exists(temp_vector):
            os.remove(temp_vector)

        ogr2ogr_cmd = [
            'ogr2ogr',
            '-f', 'GPKG',
            temp_vector,
            gdb_path,
            '-dialect', 'SQLite',
            '-sql', sql_query
        ]
        try:
            subprocess.run(ogr2ogr_cmd, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Error ogr2ogr for region={region}, period={args.period}: {e}")
            continue

        # Check features
        check_cmd = ['ogrinfo', '-ro', '-al', '-so', temp_vector]
        result = subprocess.run(check_cmd, capture_output=True, text=True)
        if 'Feature Count: 0' in result.stdout:
            logging.warning(f"No features found for region={region}, period={args.period}")
            os.remove(temp_vector)
            continue

        # 3B) Rasterize
        raster_cmd = [
            'gdal_rasterize',
            '-a', 'damage_val',
            '-tr', str(nl_res[0]), str(nl_res[1]),
            '-te', str(nl_bounds.left), str(nl_bounds.bottom),
                   str(nl_bounds.right), str(nl_bounds.top),
            '-ot', 'Int16',
            '-co', 'COMPRESS=LZW',
            '-co', 'TILED=YES',
            '-co', 'BIGTIFF=YES',
            '-a_nodata', '0',
            '-a_srs', nl_crs.to_wkt(),
            temp_vector,
            output_raster
        ]
        logging.info(f"Rasterizing => {output_raster}")
        try:
            subprocess.run(raster_cmd, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Error rasterizing region={region}, period={args.period}: {e}")
            continue
        finally:
            if os.path.exists(temp_vector):
                os.remove(temp_vector)

    logging.info("All region-level insect/disease rasters created.")


if __name__ == "__main__":
    main()
