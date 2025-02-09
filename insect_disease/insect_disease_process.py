import os
import subprocess
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
gdb_directory = r"C:\GIS\Data\LEARN\Disturbances\ADS"
nlcd_raster_path = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"
output_directory = r"C:\GIS\Data\LEARN\Disturbances\ADS\Processed"
os.makedirs(output_directory, exist_ok=True)

# Regions, e.g. [1,2,3,4,5,6,8,9]
regions = [1, 2, 3, 4, 5, 6, 8, 9]

# Example time periods
time_periods = {
    '2021_2023': [2021, 2022, 2023],
}

logging.info("Insect/Disease Extraction Script Started.")

# 1) Read NLCD metadata using rasterio (small snippet)
import rasterio
with rasterio.open(nlcd_raster_path) as nl:
    nl_bounds = nl.bounds
    nl_res = nl.res
    nl_crs = nl.crs

logging.info(f"NLCD: bounds={nl_bounds}, res={nl_res}, crs={nl_crs}")

# 2) Region-by-region
for region in regions:
    gdb_folder_name = f"CONUS_Region{region}_AllYears.gdb"
    gdb_path = os.path.join(gdb_directory, gdb_folder_name, gdb_folder_name)

    if not os.path.exists(gdb_path):
        logging.warning(f"GDB not found for region={region}: {gdb_path}")
        continue

    # Run ogrinfo to find layer
    try:
        ogrinfo_cmd = ['ogrinfo', '-ro', '-so', '-al', gdb_path]
        result = subprocess.run(ogrinfo_cmd, capture_output=True, text=True, check=True)
        layer_list = [line.strip().split(':')[-1].strip()
                      for line in result.stdout.split('\n')
                      if 'Layer name:' in line]
    except subprocess.CalledProcessError as e:
        logging.error(f"Error running ogrinfo on {gdb_path}: {e}")
        continue

    # find layer containing 'DAMAGE_AREAS_FLAT' and f"Rgn{region}"
    layer_name = next((ly for ly in layer_list if 'DAMAGE_AREAS_FLAT' in ly and f"Rgn{region}" in ly), None)
    if not layer_name:
        logging.warning(f"No matching layer found for region={region}")
        continue

    logging.info(f"Region={region}, using layer={layer_name}")

    # 3) For each time period, produce region-level raster
    for period_name, years in time_periods.items():
        output_raster = os.path.join(output_directory, f"insect_damage_{region}_{period_name}.tif")
        if os.path.exists(output_raster):
            logging.info(f"Already exists: {output_raster}, skipping.")
            continue

        # Build SQL: "Mortality - Previously Undocumented" => 5, else => 0
        year_str = ",".join(map(str, years))
        sql_query = (
            "SELECT *, CASE WHEN DAMAGE_TYPE = 'Mortality - Previously Undocumented' "
            "THEN 5 ELSE 0 END AS damage_val "
            f"FROM '{layer_name}' WHERE SURVEY_YEAR IN ({year_str})"
        )

        # 3a) ogr2ogr -> gpkg
        temp_vector = os.path.join(output_directory, f"temp_region{region}_{period_name}.gpkg")
        if os.path.exists(temp_vector):
            os.remove(temp_vector)

        ogr2ogr_cmd = [
            'ogr2ogr', '-f', 'GPKG',
            temp_vector,
            gdb_path,
            '-dialect', 'SQLite',
            '-sql', sql_query
        ]
        try:
            subprocess.run(ogr2ogr_cmd, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Error ogr2ogr for region={region}, period={period_name}: {e}")
            continue

        # check features
        check_cmd = ['ogrinfo', '-ro', '-al', '-so', temp_vector]
        result = subprocess.run(check_cmd, capture_output=True, text=True)
        if 'Feature Count: 0' in result.stdout:
            logging.warning(f"No features found for {region}, {period_name}")
            os.remove(temp_vector)
            continue

        # 3b) gdal_rasterize
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
            logging.error(f"Error rasterizing region={region}, period={period_name}: {e}")
            continue
        finally:
            if os.path.exists(temp_vector):
                os.remove(temp_vector)

logging.info("All region-level insect/disease rasters created.")
