import os
import time
import arcpy
import pandas as pd
from datetime import datetime as dt
from functools import reduce
from config import get_input_config, CELL_SIZE, OUTPUT_BASE_DIR
from funcs import feature_class_to_pandas_dataframe as feature_class_to_pandas_data_frame
# from funcs import convert_mog_df_to_json  # No longer needed since we're skipping JSON
from lookups import mogLookup, gapLookup

arcpy.env.overwriteOutput = True

def clipRaster(inRaster, clipGeometry):
    """
    Clip raster to mask
    :param inRaster: target raster to be clipped
    :param clipGeometry: geometry to clip raster (user AOI)
    :return outRaster: a clipped raster in memory file path
    """
    try:
        baseName = os.path.basename(inRaster)
        outRasterPath = os.path.join("memory", baseName)
    except Exception:
        outRasterPath = "memory/outRaster"

    arcpy.management.Clip(
        in_raster=inRaster,
        rectangle="#",
        in_template_dataset=clipGeometry,
        out_raster=outRasterPath,
        clipping_geometry="ClippingGeometry",
        nodata_value="0",
    )
    return outRasterPath

def createMaturityGAPRasterCon(clipped_maturity, clipped_GAP):
    """From the clipped rasters, use raster math operations to create a new raster with stratification values."""
    clipped_maturity = arcpy.Raster(clipped_maturity)
    clipped_GAP = arcpy.Raster(clipped_GAP)

    clipped_maturity = arcpy.sa.Con(
        arcpy.sa.IsNull(clipped_maturity), 1, clipped_maturity
    )

    # Reclassify maturity into 10, 20, 30, 40 based on class
    reclass_maturity = (
        arcpy.sa.Con(clipped_maturity == 1, 10, 0)
        + arcpy.sa.Con(clipped_maturity == 2, 20, 0)
        + arcpy.sa.Con(clipped_maturity == 3, 30, 0)
        + arcpy.sa.Con(clipped_maturity == 4, 40, 0)
    )

    # Reclass GAP: if null, set to 4
    reclass_GAP = arcpy.sa.Con(arcpy.sa.IsNull(clipped_GAP), 4, clipped_GAP)

    # Combine maturity and GAP (e.g., maturity=20 + GAP=3 -> 23)
    stratificationRaster = reclass_maturity + reclass_GAP
    return stratificationRaster

def ZonalSumByStratificationMaturityGAP(
    stratificationRaster,
    valueRaster,
    columnName,
    cellSize=30,
    mogClasses=mogLookup,
    gapClasses=gapLookup,
):
    """
    Use zonal stats by table to sum up the values in a raster by each maturity+GAP stratification class.
    """
    zonalStatsSum = arcpy.sa.ZonalStatisticsAsTable(
        stratificationRaster,
        "Value",
        valueRaster,
        "memory/zonalout",
        statistics_type="SUM",
    )
    zs_df = feature_class_to_pandas_data_frame(zonalStatsSum, ["VALUE", "COUNT", "SUM"])
    zs_df.columns = ["StratificationValue", "CellCount", columnName]

    zs_df["MOG_value"] = zs_df["StratificationValue"].apply(lambda x: int(x / 10))
    zs_df["GAP_value"] = zs_df["StratificationValue"].apply(lambda x: x % 10)
    zs_df["MOG_class"] = zs_df["MOG_value"].map(mogClasses)
    zs_df["GAP_class"] = zs_df["GAP_value"].map(gapClasses)

    # Convert area from CellCount to Hectares
    zs_df["Hectares"] = (zs_df["CellCount"].astype("int64") * cellSize**2) / 10000

    # Convert carbon units
    zs_df[columnName] = zs_df[columnName] / 10000 * 900
    return zs_df[
        [
            "StratificationValue",
            "MOG_class",
            "GAP_class",
            "Hectares",
            "CellCount",
            columnName,
        ]
    ]

def create_all_mog_pad_options(mog_lookup, gap_lookup):
    """
    Create a pandas dataframe with all the mog and gap lookups options (used to fill in missing values)
    """
    rows = []
    for k, v in mog_lookup.items():
        for k2, v2 in gap_lookup.items():
            strat = k * 10 + k2
            rows.append((strat, v, v2))
    df = pd.DataFrame(rows, columns=["StratificationValue", "MOG_class", "GAP_class"])
    return df

def perform_mog_analysis(input_config):
    """
    Perform the MOG + GAP analysis using provided input configuration.
    """

    inAOI = input_config["aoi"]
    forestMaturity = input_config["forest_age_raster"]   # Maturity raster
    GAP = input_config["gap_raster"]                     # GAP raster
    carbonSO = input_config["carbon_so"]                 # Soil carbon raster
    carbonSD = input_config["carbon_sd_dd_lt"]           # Dead wood carbon raster
    carbonAG = input_config["carbon_ag_bg_us"]           # Above/Below ground carbon raster
    cellSize = input_config.get("cell_size", 30)

    arcpy.AddMessage(
        f"MATURITY GAP INPUTS: {inAOI}, {forestMaturity}, {GAP}, {carbonSO}, {carbonSD}, {carbonAG}"
    )
    arcpy.AddMessage("Maturity + GAP PHASE 1: Create the maturity and GAP stratification raster")

    # Clip forest maturity & GAP rasters
    arcpy.env.snapRaster = forestMaturity
    clippedMaturity = clipRaster(forestMaturity, inAOI)
    clippedGAP = clipRaster(GAP, inAOI)

    # Create stratification raster
    categoryRaster = createMaturityGAPRasterCon(clippedMaturity, clippedGAP)
    clippedCategoryRaster = arcpy.Raster(clipRaster(categoryRaster, inAOI))

    arcpy.AddMessage("Maturity + GAP PHASE 2: Zonal statistics for carbon rasters")

    # Zonal statistics for each carbon component
    carbon_so_df = ZonalSumByStratificationMaturityGAP(clippedCategoryRaster, carbonSO, "carbon_so", cellSize)
    carbon_ag_df = ZonalSumByStratificationMaturityGAP(clippedCategoryRaster, carbonAG, "carbon_ag", cellSize)
    carbon_sd_df = ZonalSumByStratificationMaturityGAP(clippedCategoryRaster, carbonSD, "carbon_sd", cellSize)

    arcpy.AddMessage("Maturity + GAP PHASE 3: Combine results")

    # All possible categories
    all_categories = create_all_mog_pad_options(mogLookup, gapLookup)

    # Merge all dataframes
    carbon_df = reduce(
        lambda left, right: pd.merge(
            left,
            right,
            on=["StratificationValue", "MOG_class", "GAP_class"],
            how="left",
        ),
        [all_categories, carbon_so_df, carbon_ag_df, carbon_sd_df],
    )

    # Drop duplicate columns created during merges
    carbon_df = carbon_df.drop(
        [col for col in carbon_df.columns if col.endswith("_x") or col.endswith("_y")],
        axis=1,
    )

    # Sort and fill missing
    carbon_df.sort_values("StratificationValue", inplace=True)
    carbon_df.fillna(0, inplace=True)

    # Reorder columns
    carbon_df = carbon_df[
        [
            "StratificationValue",
            "MOG_class",
            "GAP_class",
            "Hectares",
            "CellCount",
            "carbon_so",
            "carbon_ag",
            "carbon_sd",
        ]
    ]

    return carbon_df

def main():
    # Get user inputs
    year1 = input("Enter Year 1: ").strip()
    year2 = input("Enter Year 2: ").strip()
    aoi_name = input("Enter the AOI name: ").strip()

    # For MOG analysis, we may not need tree canopy sources, but let's handle if needed
    tree_canopy_source = input("Select Tree Canopy source (NLCD, CBW, Local or None): ").strip()
    if tree_canopy_source.lower() == "none":
        tree_canopy_source = None

    # Retrieve input configuration
    input_config = get_input_config(year1, year2, aoi_name, tree_canopy_source)
    input_config["cell_size"] = CELL_SIZE
    input_config["year1"] = int(year1)
    input_config["year2"] = int(year2)

    # Ensure these keys are properly defined in the config or set them here if needed:
    # input_config["forest_age_raster"] = <path to forest maturity raster>
    # input_config["gap_raster"] = <path to GAP raster>
    # input_config["carbon_so"] = <path to soil carbon raster>
    # input_config["carbon_sd_dd_lt"] = <path to dead wood carbon raster>
    # input_config["carbon_ag_bg_us"] = <path to above/below ground carbon raster>

    # Start timing
    start_time = dt.now()

    # Create output directory
    date_str = start_time.strftime("%Y_%m_%d_%H_%M")
    output_folder_name = f"{date_str}_{year1}_{year2}_{aoi_name}_MOG"
    output_path = os.path.join(OUTPUT_BASE_DIR, output_folder_name)
    os.makedirs(output_path, exist_ok=True)

    # Save configuration
    with open(os.path.join(output_path, "config.txt"), "w") as config_file:
        config_file.write(str(input_config))

    # Run MOG analysis
    result_df = perform_mog_analysis(input_config)

    # Save CSV only (no JSON)
    csv_filename = f"mog_gap_{time.strftime('%Y%m%d_%H%M')}.csv"
    result_df.to_csv(os.path.join(output_path, csv_filename), index=False)

    arcpy.AddMessage(f"CSV file saved to {os.path.join(output_path, csv_filename)}")
    arcpy.AddMessage(f"Total processing time: {dt.now() - start_time}")

if __name__ == "__main__":
    main()
