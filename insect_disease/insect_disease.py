import os
import subprocess
import logging
import rasterio
import sys
import numpy as np  # Not actually needed now since we skip Python reclassification

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set to logging.DEBUG for more detailed output
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('insect_disease_rasterization.log'),
        logging.StreamHandler()
    ]
)

logging.info('Insect and Disease Rasterization Script Started.')

# Define paths
gdb_directory = r"C:\GIS\Data\LEARN\Disturbances\ADS"  # Update with your GDB parent directory
nlcd_raster_path = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"  # Update with your NLCD raster path
output_directory = r"C:\GIS\Data\LEARN\Disturbances\ADS\Processed"  # Update with your output directory

# Ensure output directory exists
os.makedirs(output_directory, exist_ok=True)
logging.info(f'Output directory set to: {output_directory}')

# List of regions to process
regions = [1, 2, 3, 4, 5, 6, 8, 9]
logging.info(f'Regions to process: {regions}')

# Define NLCD time periods
time_periods = {
    '2021_2023': [2021, 2022, 2023],
}
logging.info('Time periods defined.')

# Load NLCD raster to get extent, resolution, and CRS
try:
    with rasterio.open(nlcd_raster_path) as nlcd_src:
        nlcd_bounds = nlcd_src.bounds
        nlcd_crs = nlcd_src.crs
        nlcd_transform = nlcd_src.transform
        nlcd_width = nlcd_src.width
        nlcd_height = nlcd_src.height
        nlcd_res = nlcd_src.res
    logging.info('NLCD raster loaded successfully.')
except Exception as e:
    logging.error(f'Error loading NLCD raster: {e}')
    raise e

# ------------------------------------------------------------------
# STEP 1: Region-by-Region Extraction & Rasterization
# ------------------------------------------------------------------
for region in regions:
    # Construct the GDB path
    gdb_folder_name = f'CONUS_Region{region}_AllYears.gdb'
    gdb_folder_path = os.path.join(gdb_directory, gdb_folder_name)
    gdb_name = f'CONUS_Region{region}_AllYears.gdb'
    gdb_path = os.path.join(gdb_folder_path, gdb_name)

    logging.info(f'\nProcessing GDB for Region {region}')
    logging.info(f'GDB path: {gdb_path}')

    # Check if the GDB exists
    if not os.path.exists(gdb_path):
        logging.warning(f'GDB not found: {gdb_path}')
        continue

    # List all layers in the GDB using ogrinfo
    try:
        ogrinfo_command = ['ogrinfo', '-ro', '-so', '-al', gdb_path]
        result = subprocess.run(ogrinfo_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logging.error(f'Error running ogrinfo: {result.stderr}')
            continue
        layers_output = result.stdout
        layers = [line.strip().split(':')[-1].strip() for line in layers_output.split('\n') if 'Layer name:' in line]
        logging.debug(f'Layers in Region {region} GDB: {layers}')
    except Exception as e:
        logging.error(f'Error accessing {gdb_path}: {e}')
        continue

    # Find the layer that contains 'DAMAGE_AREAS_FLAT' and matches the region
    layer_name = next((layer for layer in layers if 'DAMAGE_AREAS_FLAT' in layer and f'Rgn{region}' in layer), None)

    if layer_name:
        logging.info(f'Selected layer for processing: {layer_name}')

        # Process each time period
        for period_name, years in time_periods.items():
            logging.info(f'Processing time period: {period_name} for Region {region}')

            # Define the output raster path for the region and time period
            output_raster = os.path.join(output_directory, f'insect_damage_{region}_{period_name}.tif')
            logging.info(f'Checking if output raster exists: {output_raster}')

            # Check if the output raster already exists
            if os.path.exists(output_raster):
                logging.info(f'Output raster for Region {region}, time period {period_name} already exists. Skipping processing.')
                continue

            # Build SQL query to filter SURVEY_YEAR and directly reclassify
            # 'Mortality - Previously Undocumented' => 5, else => 0
            years_str = ','.join(map(str, years))
            sql_query = (
                "SELECT *, CASE "
                "WHEN DAMAGE_TYPE = 'Mortality - Previously Undocumented' THEN 5 "
                "ELSE 0 "
                "END AS damage_val "
                f"FROM '{layer_name}' WHERE SURVEY_YEAR IN ({years_str})"
            )

            # Temporary vector file for the region and time period
            temp_vector = os.path.join(output_directory, f'temp_region{region}_{period_name}.gpkg')

            # Remove existing temporary vector file if it exists
            if os.path.exists(temp_vector):
                os.remove(temp_vector)
                logging.debug(f'Existing temporary file {temp_vector} deleted.')

            # Build ogr2ogr command to extract data
            ogr2ogr_cmd = [
                'ogr2ogr',
                '-f', 'GPKG',
                temp_vector,
                gdb_path,
                '-dialect', 'SQLite',
                '-sql', sql_query
            ]

            logging.info(f'Running ogr2ogr command for Region {region}, Period {period_name}')
            logging.debug(f'ogr2ogr command: {" ".join(ogr2ogr_cmd)}')
            try:
                subprocess.run(ogr2ogr_cmd, check=True)
                logging.info(f'Data extracted to temporary file for Region {region}, Period {period_name}')
            except subprocess.CalledProcessError as e:
                logging.error(f'Error processing data for Region {region}, Period {period_name}: {e}')
                continue

            # Check if temporary vector has features
            try:
                ogrinfo_cmd = ['ogrinfo', '-ro', '-al', '-so', temp_vector]
                result = subprocess.run(ogrinfo_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if 'Feature Count: 0' in result.stdout:
                    logging.warning(f'No features found for Region {region}, Period {period_name}. Skipping rasterization.')
                    os.remove(temp_vector)
                    continue
            except Exception as e:
                logging.error(f'Error checking features in temporary file for Region {region}, Period {period_name}: {e}')
                continue

            # Rasterize the data to create the output raster for the region/time period
            # Note: We output Int16 with nodata=0, so each pixel is either 5 or 0
            cmd = [
                'gdal_rasterize',
                '-a', 'damage_val',
                '-tr', str(nlcd_res[0]), str(nlcd_res[1]),
                '-te', str(nlcd_bounds.left), str(nlcd_bounds.bottom),
                       str(nlcd_bounds.right), str(nlcd_bounds.top),
                '-ot', 'Int16',           # 16-bit integer
                '-co', 'COMPRESS=LZW',
                '-co', 'TILED=YES',
                '-co', 'BIGTIFF=YES',     # Allows outputting large files
                '-a_nodata', '0',         # 0 is our NoData
                '-a_srs', nlcd_crs.to_wkt(),
                temp_vector,
                output_raster
            ]

            logging.info(f'Rasterizing data for Region {region}, Period {period_name}')
            logging.debug(f'gdal_rasterize command: {" ".join(cmd)}')
            try:
                subprocess.run(cmd, check=True)
                logging.info(f'Rasterization complete for Region {region}, Period {period_name}. Output saved to {output_raster}')
            except subprocess.CalledProcessError as e:
                logging.error(f'Error rasterizing data for Region {region}, Period {period_name}: {e}')
                continue
            finally:
                # Clean up temporary vector file
                if os.path.exists(temp_vector):
                    os.remove(temp_vector)
                    logging.debug(f'Temporary file {temp_vector} deleted.')
    else:
        logging.warning(f'Layer containing "DAMAGE_AREAS_FLAT" not found in GDB for Region {region}')
        continue

logging.info('All regions processed.')

# ------------------------------------------------------------------
# STEP 2: Merge Regional Rasters for Each Time Period
# ------------------------------------------------------------------
for period_name in time_periods.keys():
    logging.info(f'\nMerging rasters for time period: {period_name}')

    # Define the output merged raster path
    merged_raster = os.path.join(output_directory, f'insect_damage_{period_name}.tif')
    logging.info(f'Checking if merged raster exists: {merged_raster}')

    # Check if the merged raster already exists
    if os.path.exists(merged_raster):
        logging.info(f'Merged raster for time period {period_name} already exists. Skipping merging.')
        continue

    # Collect raster paths for this time period
    raster_paths = []
    for region in regions:
        raster_path = os.path.join(output_directory, f'insect_damage_{region}_{period_name}.tif')
        if os.path.exists(raster_path):
            raster_paths.append(raster_path)
        else:
            logging.warning(f'Raster for Region {region}, Period {period_name} does not exist and will not be included in the merge.')

    if not raster_paths:
        logging.warning(f'No rasters found for time period {period_name}. Skipping merging.')
        continue

    # Create a text file listing all raster paths
    raster_list_file = os.path.join(output_directory, f'raster_list_{period_name}.txt')
    with open(raster_list_file, 'w') as f:
        for raster_path in raster_paths:
            f.write(f'{raster_path}\n')

    # Create a virtual raster (VRT) using gdalbuildvrt
    vrt_path = os.path.join(output_directory, f'insect_damage_{period_name}.vrt')
    gdalbuildvrt_cmd = [
        'gdalbuildvrt',
        '-input_file_list', raster_list_file,
        vrt_path
    ]

    logging.info(f'Building VRT for time period {period_name}')
    logging.debug(f'gdalbuildvrt command: {" ".join(gdalbuildvrt_cmd)}')
    try:
        subprocess.run(gdalbuildvrt_cmd, check=True)
        logging.info(f'VRT built at {vrt_path}')
    except subprocess.CalledProcessError as e:
        logging.error(f'Error building VRT for time period {period_name}: {e}')
        continue

    # Convert VRT to GeoTIFF using gdal_translate with compression
    # These data are already Int16 (5 or 0), so no reclassification is needed
    gdal_translate_cmd = [
        'gdal_translate',
        '-of', 'GTiff',
        '-co', 'COMPRESS=LZW',
        '-co', 'TILED=YES',
        '-co', 'BIGTIFF=YES',  # Allows outputting large files
        vrt_path,
        merged_raster
    ]

    logging.info(f'Converting VRT to GeoTIFF for time period {period_name}')
    logging.debug(f'gdal_translate command: {" ".join(gdal_translate_cmd)}')
    try:
        subprocess.run(gdal_translate_cmd, check=True)
        logging.info(f'Merged raster saved to {merged_raster}')
    except subprocess.CalledProcessError as e:
        logging.error(f'Error converting VRT to GeoTIFF for time period {period_name}: {e}')
        continue
    finally:
        # Clean up temporary files
        if os.path.exists(vrt_path):
            os.remove(vrt_path)
            logging.debug(f'Temporary VRT file {vrt_path} deleted.')
        if os.path.exists(raster_list_file):
            os.remove(raster_list_file)
            logging.debug(f'Temporary raster list file {raster_list_file} deleted.')

logging.info('All merging completed.')

# No final Python reclassification needed because each regional raster
# is already 5 (mortality) or 0 (everything else), and the merged
# raster is also Int16 with the same values.

logging.info("Processing completed. No further reclassification steps required.")
