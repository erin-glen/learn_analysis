import os
import geopandas as gpd
import fiona
import pandas as pd
import rasterio
from rasterio.features import rasterize
import logging

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
gdb_directory = r"C:\GIS\Data\LEARN\Disturbances\ADS_Raw\raw_gdbs"  # Update with the path to your GDB parent directory
nlcd_raster_path = r"C:\GIS\Data\LEARN\SourceData\LandCover\nlcd_2021_land_cover_l48_20210604.tif"  # Update with the path to your NLCD raster
output_directory = r"C:\GIS\Data\LEARN\Disturbances\Output"  # Update with the path where you want to save outputs

# Ensure output directory exists
os.makedirs(output_directory, exist_ok=True)
logging.info(f'Output directory set to: {output_directory}')

# List of regions to process
regions = [1, 2, 3, 4, 5, 6, 8, 9]
logging.info(f'Regions to process: {regions}')

# Initialize an empty list to collect GeoDataFrames
gdfs = []

# Register 'OpenFileGDB' driver for fiona
fiona.drvsupport.supported_drivers['OpenFileGDB'] = 'r'

# Loop through each region and load the data
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

    # List all layers in the GDB
    try:
        layers = fiona.listlayers(gdb_path)
        logging.debug(f'Layers in Region {region} GDB: {layers}')
    except Exception as e:
        logging.error(f'Error accessing {gdb_path}: {e}')
        continue

    # Find the layer that contains 'DAMAGE_AREAS_FLAT' and matches the region
    layer_name = next((layer for layer in layers if 'DAMAGE_AREAS_FLAT' in layer and f'Rgn{region}' in layer), None)

    if layer_name:
        logging.info(f'Selected layer for reading: {layer_name}')
        # Read the layer into a GeoDataFrame
        try:
            gdf = gpd.read_file(gdb_path, layer=layer_name, driver='OpenFileGDB')
            logging.info(f'Data loaded for region {region}. Number of records: {len(gdf)}')
        except Exception as e:
            logging.error(f'Error reading layer {layer_name} in GDB {gdb_name}: {e}')
            continue

        # Select required columns
        if {'DAMAGE_TYPE', 'SURVEY_YEAR', 'geometry'}.issubset(gdf.columns):
            gdf = gdf[['DAMAGE_TYPE', 'SURVEY_YEAR', 'geometry']]
            gdfs.append(gdf)
            logging.debug(f'Required columns selected for region {region}')
        else:
            missing_cols = {'DAMAGE_TYPE', 'SURVEY_YEAR', 'geometry'}.difference(gdf.columns)
            logging.warning(f'Missing columns {missing_cols} in layer {layer_name}')
    else:
        logging.warning(f'Layer containing "DAMAGE_AREAS_FLAT" not found in GDB for Region {region}')

# Combine all GeoDataFrames into one
if not gdfs:
    logging.error('No data loaded. Please check your data files and paths.')
    raise ValueError('No data loaded. Please check your data files and paths.')

combined_gdf = pd.concat(gdfs, ignore_index=True)
logging.info(f'Combined GeoDataFrame created. Total records: {len(combined_gdf)}')

# Load NLCD raster to get CRS and metadata
try:
    with rasterio.open(nlcd_raster_path) as nlcd_src:
        nlcd_crs = nlcd_src.crs
        nlcd_transform = nlcd_src.transform
        nlcd_shape = (nlcd_src.height, nlcd_src.width)
        nlcd_meta = nlcd_src.meta.copy()
    logging.info('NLCD raster loaded successfully.')
except Exception as e:
    logging.error(f'Error loading NLCD raster: {e}')
    raise e

# Ensure the CRS of the combined GeoDataFrame matches the NLCD raster
logging.info('Ensuring CRS consistency between vector data and NLCD raster.')
try:
    combined_gdf = combined_gdf.to_crs(nlcd_crs)
    logging.info('CRS transformed successfully.')
except Exception as e:
    logging.error(f'Error transforming CRS: {e}')
    raise e

# Define reclassification function
def classify_damage(row):
    if row['DAMAGE_TYPE'] == 'Mortality - Previously Undocumented':
        return 2
    else:
        return 1

# Apply classification
logging.info('Applying damage classification.')
combined_gdf['damage_val'] = combined_gdf.apply(classify_damage, axis=1)
logging.debug('Damage classification applied.')

# Remove invalid geometries
initial_count = len(combined_gdf)
combined_gdf = combined_gdf[combined_gdf.is_valid]
invalid_count = initial_count - len(combined_gdf)
if invalid_count > 0:
    logging.warning(f'Removed {invalid_count} invalid geometries.')
else:
    logging.info('No invalid geometries found.')

# Define NLCD time periods
time_periods = {
    '2001_2004': [2001, 2002, 2003, 2004],
    '2004_2006': [2004, 2005, 2006],
    '2006_2008': [2006, 2007, 2008],
    '2008_2011': [2008, 2009, 2010, 2011],
    '2011_2013': [2011, 2012, 2013],
    '2013_2016': [2013, 2014, 2015, 2016],
    '2016_2019': [2016, 2017, 2018, 2019],
    '2019_2021': [2019, 2020, 2021],
}
logging.info('Time periods defined.')

# Process each time period
for period_name, years in time_periods.items():
    logging.info(f'Processing period: {period_name}')
    # Filter data for the current time period
    period_gdf = combined_gdf[combined_gdf['SURVEY_YEAR'].isin(years)]
    record_count = len(period_gdf)
    logging.info(f'Number of records for period {period_name}: {record_count}')

    if period_gdf.empty:
        logging.warning(f'No data for period {period_name}')
        continue

    # Prepare shapes and values for rasterization
    shapes = ((geom, value) for geom, value in zip(period_gdf.geometry, period_gdf.damage_val))
    logging.debug(f'Prepared shapes for rasterization for period {period_name}.')

    # Rasterize
    logging.info(f'Rasterizing data for period {period_name}.')
    try:
        rasterized = rasterize(
            shapes=shapes,
            out_shape=nlcd_shape,
            fill=0,  # NoData value
            transform=nlcd_transform,
            dtype='uint8',
            all_touched=False  # Change to True if needed
        )
        logging.info(f'Rasterization complete for period {period_name}.')
    except Exception as e:
        logging.error(f'Error rasterizing data for period {period_name}: {e}')
        continue

    # Update metadata
    out_meta = nlcd_meta.copy()
    out_meta.update({
        'driver': 'GTiff',
        'dtype': 'uint8',
        'compress': 'lzw',
        'count': 1,
        'nodata': 0
    })
    logging.debug(f'Metadata updated for output raster of period {period_name}.')

    # Save the raster
    output_raster = os.path.join(output_directory, f'insect_damage_{period_name}.tif')
    try:
        with rasterio.open(output_raster, 'w', **out_meta) as dest:
            dest.write(rasterized, 1)
        logging.info(f'Raster for period {period_name} saved to {output_raster}')
    except Exception as e:
        logging.error(f'Error saving raster for period {period_name}: {e}')
        continue

logging.info('Processing completed.')
