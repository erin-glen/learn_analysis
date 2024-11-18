# helper functions used in multiple parts of the gp tool
import arcpy
import os
import pandas as pd
from datetime import datetime
from lookups import nlcdParentRollupCategories
from funcs import (
    tabulateAreaByStratification,
    calculate_category,
    save_results,
    calculate_FRF,
    fillNA,
    disturbanceMax,
    zonal_sum_carbon,
    landuseStratificationRaster,
    calculateDisturbances,
    mergeAgeFactors,
    calculateFNF,
    summarize_ghg,
    create_matrix,
    write_dataframes_to_csv,
)

arcpy.env.overwriteOutput = True

# pandas options
pd.options.mode.chained_assignment = None  # suppress chained assignment warnings

# Check out the Spatial Analyst extension (required)
if arcpy.CheckExtension("Spatial") == "Available":
    arcpy.CheckOutExtension("Spatial")
else:
    raise Exception("Spatial Analyst extension is not available.")


def main(
    aoi,
    nlcd_1,
    nlcd_2,
    forestAgeRaster,
    carbon_ag_bg_us,
    carbon_sd_dd_lt,
    carbon_so,
    forest_lookup_csv,
    disturbanceRasters,
    cellsize,
    year1,
    year2,
    geography_id,
):
    """
    Land use change stratification summaries for forest age, carbon, and disturbance.
    """
    try:
        # Save original environment settings
        original_extent = arcpy.env.extent

        # Get the extent of the AOI
        aoi_extent = arcpy.Describe(aoi).extent

        # Get the extent of the input raster (e.g., NLCD)
        nlcd_extent = arcpy.Describe(nlcd_1).extent

        # Check if the AOI extent overlaps with the NLCD extent
        if aoi_extent.disjoint(nlcd_extent):
            arcpy.AddWarning(f"The AOI extent for geography ID {geography_id} does not overlap with the input rasters.")
            return None, None

        # Set the environment extent to the AOI's extent
        arcpy.env.extent = aoi_extent

        # arcpy environment settings
        arcpy.env.snapRaster = nlcd_1
        arcpy.env.cellSize = cellsize
        arcpy.env.overwriteOutput = True

        # Print out the paths to all the inputs
        arcpy.AddMessage(
            f"INPUTS for Geography ID {geography_id}: {aoi}, {nlcd_1}, {nlcd_2}, {forestAgeRaster}, "
            f"{carbon_ag_bg_us}, {carbon_sd_dd_lt}, {carbon_so}, {disturbanceRasters}"
        )

        # Stratification raster
        arcpy.AddMessage("STEP 1: Creating land use stratification raster for all classes of land use")
        stratRast = landuseStratificationRaster(nlcd_1, nlcd_2, aoi)

        # Disturbance - tabulate the area
        arcpy.AddMessage("STEP 2: Cross-tabulating disturbance area by stratification class")
        arcpy.AddMessage(f"Number of disturbance rasters: {len(disturbanceRasters)}")
        disturbance_wide, disturbRast = disturbanceMax(disturbanceRasters, stratRast)

        # Carbon - zonal sum
        arcpy.AddMessage("STEP 3: Zonal statistics sum for carbon rasters by stratification class")
        carbon = zonal_sum_carbon(stratRast, carbon_ag_bg_us, carbon_sd_dd_lt, carbon_so)

        # Merge disturbance area and carbon totals by stratification class
        groupByLanduseChangeDF = carbon.merge(
            disturbance_wide, how='outer', on=["StratificationValue", "NLCD1_class", "NLCD2_class"]
        )

        groupByLanduseChangeDF["NLCD_1_ParentClass"] = groupByLanduseChangeDF["NLCD1_class"].map(nlcdParentRollupCategories)
        groupByLanduseChangeDF["NLCD_2_ParentClass"] = groupByLanduseChangeDF["NLCD2_class"].map(nlcdParentRollupCategories)
        groupByLanduseChangeDF['Category'] = groupByLanduseChangeDF.apply(calculate_category, axis=1)
        groupByLanduseChangeDF['Total Emissions Forest to Non Forest CO2'] = groupByLanduseChangeDF.apply(
            calculateFNF, axis=1
        )

        # Forest Age Type - tabulate the area
        arcpy.AddMessage("STEP 4: Tabulating total area for the forest age types by stratification class")
        forestAge = tabulateAreaByStratification(stratRast, forestAgeRaster, outputName="ForestAgeTypeRegion")

        # Merge forestAge area total + disturbance areas by forestAge
        arcpy.AddMessage(
            "STEP 5: Tabulating disturbance area for the forest age types by stratification class for fires"
        )
        forestAge = calculateDisturbances(disturbRast, stratRast, forestAgeRaster, forestAge)

        # Fill empty cells with zero for calculations
        forestAge = fillNA(forestAge)

        forestAge = mergeAgeFactors(forestAge, forest_lookup_csv)

        arcpy.AddMessage(
            "STEP 6: Calculating emissions from disturbances and removals from undisturbed forests / non-forest to forest"
        )
        # Calculate emissions from disturbances, removals from forests remaining forests,
        # and removals from non-forest to forest using function
        calculate_FRF(forestAge, year1, year2)

        return (
            groupByLanduseChangeDF.sort_values(by=["Hectares"], ascending=False),
            forestAge.sort_values(by=["Hectares"], ascending=False),
        )

    except Exception as e:
        arcpy.AddError(f"An error occurred in main() for geography ID {geography_id}: {e}")
        return None, None

    finally:
        # Restore the original environment settings
        arcpy.env.extent = original_extent


if __name__ == "__main__":
    wd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(wd)

    # Define data folders
    dataFolder = r"U:\eglen\Projects\LEARN Tools\Data\SourceData\Data\Rasters"  # Update as needed
    alternateDataFolder = r"U:\eglen\Projects\LEARN Tools\Data\AlternateData"

    # User inputs
    years_list = ["2001", "2004", "2006", "2008", "2011", "2013", "2016", "2019", "2021"]
    year1 = str(input("Year 1: "))
    assert year1 in years_list, f"{year1} is not in the list of valid inputs."
    year2 = str(input("Year 2: "))
    assert year2 in years_list, f"{year2} is not in the list of valid inputs."

    cellsize = 30

    # Path to the shapefile containing multiple geographies
    aoi_shapefile = r"U:\eglen\Data\PADUS4_0Geodatabase\PADUS_BLM_USFS_STATE_PRJ.shp"  # Update this path

    # Field in the shapefile that uniquely identifies each geography
    id_field = 'FID'  # Update to the correct field name in your shapefile

    # Prepare input configuration
    inputConfig = dict(
        nlcd_1=os.path.join(dataFolder, "LandCover", f"NLCD_{year1}_Land_Cover_l48_20210604.tif"),
        nlcd_2=os.path.join(dataFolder, "LandCover", f"NLCD_{year2}_Land_Cover_l48_20210604.tif"),
        forestAgeRaster=os.path.join(dataFolder, "ForestType", "forest_raster_07232020.tif"),
        carbon_ag_bg_us=os.path.join(dataFolder, "Carbon", "carbon_ag_bg_us.tif"),
        carbon_sd_dd_lt=os.path.join(dataFolder, "Carbon", "carbon_sd_dd_lt.tif"),
        carbon_so=os.path.join(dataFolder, "Carbon", "carbon_so.tif"),
        forest_lookup_csv=os.path.join(dataFolder, "ForestType", "forest_raster_09172020.csv"),
        disturbanceRasters=[
            os.path.join(dataFolder, "Disturbances", "disturbance_1921.tif")
        ],
        cellsize=cellsize,
        year1=year1,
        year2=year2,
    )

    startTime = datetime.now()

    # Define the output directory
    parentOutputDirectory = r"U:\eglen\Projects\LEARN Tools\Data\Outputs"
    dateFormat = startTime.strftime("%m_%d")
    outputFolderName = f"{dateFormat}_{year1}_{year2}_BatchProcessing"
    outputPath = os.path.join(parentOutputDirectory, outputFolderName)

    if not os.path.exists(outputPath):
        os.makedirs(outputPath)

    text_doc = os.path.join(outputPath, "doc.txt")
    with open(text_doc, 'w') as doc:
        doc.write(f"Year 1: {year1}\n")
        doc.write(f"Year 2: {year2}\n")
        doc.write(f"Cellsize: {cellsize}\n")
        doc.write(f"Date: {datetime.now()}\n\n")
        doc.write(str(inputConfig))

    # Initialize an empty list to collect results
    all_results = []

    # Loop through each feature (geography) in the shapefile
    with arcpy.da.SearchCursor(aoi_shapefile, [id_field, 'SHAPE@']) as cursor:
        for row in cursor:
            geography_id = row[0]
            geometry = row[1]

            print(f"Processing geography ID: {geography_id}")

            try:
                # Create a temporary feature class for the current geography
                aoi_temp = arcpy.management.CopyFeatures(geometry, "in_memory\\aoi_temp")

                # Update the inputConfig dictionary with the current AOI and geography ID
                inputConfig["aoi"] = aoi_temp
                inputConfig["geography_id"] = geography_id

                # Run the main processing function
                landuse_result, forestType_result = main(**inputConfig)

                if landuse_result is None or forestType_result is None:
                    print(f"Skipping geography ID {geography_id} due to overlap issues or errors.")
                    continue

                # Summarize the results for the current geography
                years = int(year2) - int(year1)
                ghg_result = summarize_ghg(landuse_result, forestType_result, years)

                # Add the geography ID to the ghg_result DataFrame
                ghg_result['Geography_ID'] = geography_id

                # Append the ghg_result to the list of all results
                all_results.append(ghg_result)

            except Exception as e:
                print(f"An error occurred processing geography ID {geography_id}: {e}")
                continue  # Skip to the next geography

            finally:
                # Clean up the in-memory AOI and reset environment settings
                arcpy.management.Delete(aoi_temp)
                arcpy.env.extent = None  # Reset the extent

    # Combine all results into a single DataFrame
    if all_results:
        combined_results = pd.concat(all_results, ignore_index=True)

        # Reorder columns to have Geography_ID first
        cols = ['Geography_ID'] + [col for col in combined_results.columns if col != 'Geography_ID']
        combined_results = combined_results[cols]

        # Save the combined results to a CSV file
        output_csv_path = os.path.join(outputPath, "combined_results.csv")
        combined_results.to_csv(output_csv_path, index=False)
    else:
        print("No results to combine. The all_results list is empty.")

    print(f"Total processing time: {datetime.now() - startTime}")
