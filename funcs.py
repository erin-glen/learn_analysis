# helper functions used in multiple parts of the gp tool
import arcpy
import pandas as pd
from lookups import nlcdParentRollupCategories, nlcdCategories, disturbanceLookup, carbonStockLoss
import numpy as np
import os

arcpy.env.overwriteOutput = True


def feature_class_to_pandas_data_frame(feature_class, field_list):
    """
	Load data into a Pandas Data Frame for subsequent analysis.
	https://joelmccune.com/arcgis-to-pandas-data-frame/
	:param feature_class: Input ArcGIS Feature Class.
	:param field_list: Fields for input.
	:return: Pandas DataFrame object.
	"""
    return pd.DataFrame(
        arcpy.da.FeatureClassToNumPyArray(
            in_table=feature_class,
            field_names=field_list,
            skip_nulls=False,
            null_value=-99999,
        )
    )


def landuseStratificationRaster(nlcdRaster1, nlcdRaster2, aoi):
    """
    Create the two year NLCD stratification raster by offsetting one year by 100 and then adding the second year.
    This allow all landuse change to be tracked between two years. Rasters must follow the NLCD classification schema.
    Example: a pixel has a value of 43 (mixed forest) in the first raster and a value 81 (Pasture/hay) in the second
    representing the transition from mixed forest to pasture, the stratification raster will have a pixel value of 4381.
    :param nlcdRaster1: the path to the NLCD raster to use for the initial state
    :param nlcdRaster2: the path the second NLCD raster to use as the after stat
    :param aoi: the area of interest
    :return: the landuse change raster where the pixel values combines both year classifcation values
    """
    # https://gis.stackexchange.com/questions/94469/is-clip-data-management-or-extract-by-mask-spatial-analyst-more-efficient
    arcpy.Clip_management(nlcdRaster1, "#", "in_memory/nlcd_before", aoi, "", "ClippingGeometry")
    arcpy.Clip_management(nlcdRaster2, "#", "in_memory/nlcd_after", aoi, "", "ClippingGeometry")
    stratRast = arcpy.Raster("in_memory/nlcd_before") * 100 + arcpy.Raster("in_memory/nlcd_after")
    return stratRast


def rollupToParentClass(df, columnsToGather, groupBy=None):
    """
	Rollup values to the standard/parent categories using sum
	:param df: dataframe
	:param columnsToGather: list of columns to gather
	:param groupBy: optional list of columns to use in the groupBy in addition to the NLCD parent classes
	:return: dataframe with values summed using the standard/parent categories
	"""
    df2 = df.copy()  # create a copy to avoid manipulating original
    # use the lookup dictionary to add the standard categories for each row
    df2["NLCD1_parentclass"] = df2["NLCD1_class"].map(nlcdParentRollupCategories)
    df2["NLCD2_parentclass"] = df2["NLCD2_class"].map(nlcdParentRollupCategories)
    groupColumns = ["NLCD1_parentclass", "NLCD2_parentclass"]
    if groupBy is not None:
        groupColumns += groupBy
    gb = df2.groupby(groupColumns)[columnsToGather].sum().reset_index()
    return gb


def showPivot(df, values, index_category, column_category):
    """
	Pivot dataframe like an excel pivot table for display using the value sums
	:param df: dataframe
	:param values: column to sum
	:param index_category: the column to use as index (vertical)
	:param column_category: the column to use as columns (horizontal)
	:return: pivoted dataframe
	"""
    cell_count_pivot = pd.pivot_table(df, values=values, index=[index_category], columns=[column_category],
                                      aggfunc=np.sum)
    return cell_count_pivot


def tabulateAreaByStratification(stratificationRaster, valueRaster, outputName, pixelSize=30, colNameArea="Hectares"):
    """
	Tabulate the forest age type values for each of the stratification raster land use change classes
	:param stratificationRaster: the stratification raster (clipped to the AOI)
	:param valueRaster: path to the second raster to tabulate area
	:param outputName: name for the column that stores the valueRaster values
	:param pixelSize: pixel size for tabulating error (default is 30 meters)
	:param colNameArea: string for the column name that contains the area (units are hectares)
	:return: pandas dataframe with the area in HA for all of the value raster types for each stratification type
	"""
    # use the tabulate area spatial analyst tool to get the cross tabulated area for combinations
    crossTab = arcpy.sa.TabulateArea(stratificationRaster, "Value", valueRaster, "Value",
                                     "in_memory/crossTab", pixelSize, )
    # convert the results from the tabulation area to a pandas dataframe (all columns)
    crossTab_df = feature_class_to_pandas_data_frame(crossTab, "*")
    # convert wide data into long by gethering all of the forest type columns
    df_melt = pd.melt(crossTab_df, id_vars=["OBJECTID", "VALUE"], value_name="Area")
    # extract just the raster value from the variable column ("VALUE_123" -> 123)
    df_melt["Raster_VALUE"] = df_melt["variable"].apply(
        lambda x: int(x.split("_")[1])
    )  # grab just the number

    # unpack the NLCD codes from the stratification raster
    df_melt["NLCD1_value"] = df_melt["VALUE"].apply(lambda x: int(x / 100))
    df_melt["NLCD2_value"] = df_melt["VALUE"].apply(lambda x: x % 100)

    # Use the NLCD categories dictionary to get the NLCD categories
    df_melt["NLCD1_class"] = df_melt["NLCD1_value"].map(nlcdCategories)
    df_melt["NLCD2_class"] = df_melt["NLCD2_value"].map(nlcdCategories)

    # remove any rows with no area
    df_melt_filter = df_melt[df_melt["Area"] > 0]  # removes any rows without any area
    df_melt_filter[colNameArea] = df_melt_filter.Area / 10000
    df_melt_filter.rename(columns={"Raster_VALUE": outputName, "VALUE": "StratificationValue"}, inplace=True)

    if arcpy.Exists("in_memory/crossTab"):
        arcpy.Delete_management("in_memory/crossTab")

    return df_melt_filter[["StratificationValue", "NLCD1_class", "NLCD2_class", outputName, colNameArea]]


def ZonalSumByStratification(stratificationRaster, valueRaster, columnName, cellSize=30):
    """
    Use zonal stats by table to sum up the values in a raster by each landuse stratifiction class
    :param stratificationRaster: the landuse stratification raster (clipped to AOI)
    :param valueRaster: the raster to summarize
    :param columnName: the column name to store the totals
    :param cellSize: the processing cell size (default is 30 meters)
    :return: pandas dataframe with the stratification classes and the total summed up
    """
    # zonal stats to sum up the average canopy values per stratification class
    zonalStatsSum = arcpy.sa.ZonalStatisticsAsTable(
        stratificationRaster,
        "Value",
        valueRaster,
        "in_memory/zonalout1",
        statistics_type="SUM",
    )

    # convert to pandas dataframe
    zs_df = feature_class_to_pandas_data_frame(zonalStatsSum, ["VALUE", "COUNT", "SUM"])

    # dataframe clean up
    zs_df.columns = ["StratificationValue", "CellCount", columnName]
    zs_df["NLCD1_value"] = zs_df["StratificationValue"].apply(lambda x: int(x / 100))
    zs_df["NLCD2_value"] = zs_df["StratificationValue"].apply(lambda x: x % 100)
    zs_df["NLCD1_class"] = zs_df["NLCD1_value"].map(nlcdCategories)
    zs_df["NLCD2_class"] = zs_df["NLCD2_value"].map(nlcdCategories)

    # area in meters squared is the cell count times cell size (30 x 30 meters). Divide by 10k to get hectares
    zs_df["Hectares"] = (zs_df["CellCount"].astype('int64') * cellSize ** 2) / 10000

    if arcpy.Exists("in_memory/zonalout1"):
        arcpy.Delete_management("in_memory/zonalout1")

    return zs_df[["StratificationValue", "NLCD1_class", "NLCD2_class", "Hectares", "CellCount", columnName]]


def calculate_treeCanopy(treecanopy_1, treecanopy_2, stratRast, tree_canopy, aoi, cellsize):
    # mask the tree canopy to the area of interest
    # mask the tree canopy to the area of interest
    if treecanopy_1 is not None and treecanopy_2 is not None:

        # calculate the average tree canopy per pixel
        with arcpy.EnvManager(mask=aoi, cellSize=(int(cellsize))):
            treeCanopyAvg = (
                                    arcpy.Raster(treecanopy_1) + arcpy.Raster(treecanopy_2)
                            ) / 2  # average

        # tree canopy loss this only happens on the pixels where there is loss
        # logic would be something like Con(tree2 < tree1, tree1-tree2, 0)
        # find the different where there is tree canopy loss
        with arcpy.EnvManager(mask=aoi, cellSize=(cellsize)):
            treeCanopyDiff = arcpy.sa.Con(
                arcpy.Raster(treecanopy_2) < arcpy.Raster(treecanopy_1),
                arcpy.Raster(treecanopy_1) - arcpy.Raster(treecanopy_2),
                0,
            )

        # Optional hardcoded tree canopy loss: if using this, comment out the above tree canopy loss calculation
        # treeCanopyDiff = r"U:\eglen\Projects\LEARN Tools\Data\AlternateData\Cincinnati\TreeCanopy_Loss.tif"

        # zonal sum for each stratification class
        tc_avg_df = ZonalSumByStratification(
            stratRast, treeCanopyAvg, "TreeCanopy_HA", cellSize=30
        )
        tc_diff_df = ZonalSumByStratification(
            stratRast, treeCanopyDiff, "TreeCanopyLoss_HA", cellSize=30
        )

        # drop columns to avoid duplicate cols
        tc_diff_df.drop(["Hectares", "CellCount"], axis=1, inplace=True)
        # merge the two dataframes
        treeCover = tc_avg_df.merge(
            tc_diff_df,
            how="outer",
            on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        )

        """
            tree canopy notes...
            cell size is now set to automatically update for TC based on tree canopy selection (1,30)
            for non aggregated 1 m data: 0.0001 
            if running at 1 meter, take * 900 out of formula, so just * 0.0001)
            for aggregated data: 0.0001 (this one you can run at 30 meter cell size)
        """

        if "NLCD" in tree_canopy:
            # ie 50% is 50/100 * 900 square meters  * 0.0001 HA = 0.045 canopy in HA
            treeCover["TreeCanopy_HA"] = (
                    treeCover["TreeCanopy_HA"] / 100 * 900 * 0.0001
            )  # % canopy * pixel size * sq m to ha
            treeCover["TreeCanopyLoss_HA"] = (
                    treeCover["TreeCanopyLoss_HA"] / 100 * 900 * 0.0001
            )  # % canopy * pixel size * sq m to ha
        elif "CBW" in tree_canopy:
            # ie 50% is 50/100 * 900 square meters  * 0.0001 HA = 0.045 canopy in HA
            treeCover["TreeCanopy_HA"] = (
                    treeCover["TreeCanopy_HA"] * 0.0001
            )  # % canopy * pixel size * sq m to ha
            treeCover["TreeCanopyLoss_HA"] = (
                    treeCover["TreeCanopyLoss_HA"] * 0.0001
            )  # % canopy * pixel size * sq m to ha
        else:
            treeCover["TreeCanopy_HA"] = (
                    treeCover["TreeCanopy_HA"] * 0.0001 * 900
            )  # % canopy * pixel size * sq m to ha
            treeCover["TreeCanopyLoss_HA"] = (
                    treeCover["TreeCanopyLoss_HA"] * 0.0001 * 900
            )  # % canopy * pixel size * sq m to ha

        # drop columns to avoid duplicates names when merging"StratificationValue", "NLCD1_class", "NLCD2_class"
        treeCover.drop(["Hectares", "CellCount"], axis=1, inplace=True)
        return treeCover
    else:
        arcpy.AddMessage("Skipping Tree Canopy - no data.")
        treeCover = pd.DataFrame(columns=["StratificationValue", "NLCD1_class", "NLCD2_class",
                                          "TreeCanopy_HA", "TreeCanopyLoss_HA"])


def calculate_plantable(plantableAreas, stratRast, treeCover, aoi, cellsize):
    if plantableAreas != "None":
        # calculate the average tree canopy per pixel
        with arcpy.EnvManager(mask=aoi, cellSize=(int(cellsize))):
            plantableRaster = (arcpy.Raster(plantableAreas))

        plantable_sum = ZonalSumByStratification(stratRast, plantableRaster, "Plantable_HA", cellSize=30)

        # merge the two dataframes
        treeCover = treeCover.merge(
            plantable_sum,
            how="outer",
            on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        )

        treeCover["Plantable_HA"] = (
                treeCover["Plantable_HA"] * 0.0001 * 900)

        # drop columns to avoid duplicates names when merging"StratificationValue", "NLCD1_class", "NLCD2_class"
        treeCover.drop(["Hectares", "CellCount"], axis=1, inplace=True)

    else:
        arcpy.AddMessage("Skipping Plantable Areas - no data.")
    return treeCover


def disturbanceMax(disturbanceRasters, stratRast):
    if len(disturbanceRasters) == 1:
        disturbRast = disturbanceRasters[0]
    else:
        disturbRast = arcpy.sa.CellStatistics(disturbanceRasters, "MAXIMUM", ignore_nodata="DATA")
    disturbance = tabulateAreaByStratification(stratRast, disturbRast, outputName="Disturbance")
    disturbance["DisturbanceClass"] = disturbance["Disturbance"].map(disturbanceLookup)

    # convert disturbances to wide dataframe and sum classes that are the same together to get total area
    disturbance_wide = disturbance.pivot_table(index=['StratificationValue', 'NLCD1_class', 'NLCD2_class'],
                                               columns='DisturbanceClass', values='Hectares',
                                               aggfunc='sum').reset_index()

    if not any(x in ["fire_HA", "harvest_HA", "insect_damage_HA"] for x in disturbance_wide.columns):
        disturbance_wide = pd.DataFrame(columns=['StratificationValue', 'NLCD1_class', 'NLCD2_class'])
    return disturbance_wide, disturbRast


def zonal_sum_carbon(stratRast, carbon_ag_bg_us, carbon_sd_dd_lt, carbon_so):
    carbon_ag_bg_us_df = ZonalSumByStratification(stratRast, carbon_ag_bg_us, "carbon_ag_bg_us")
    carbon_sd_dd_lt_df = ZonalSumByStratification(stratRast, carbon_sd_dd_lt, "carbon_sd_dd_lt")
    carbon_so_df = ZonalSumByStratification(stratRast, carbon_so, "carbon_so")

    # drop columns to avoid duplicates names (hectares + areas are the same since the zones are identical)
    carbon_sd_dd_lt_df.drop(["Hectares", "CellCount"], axis=1, inplace=True)
    carbon_so_df.drop(["Hectares", "CellCount"], axis=1, inplace=True)

    # merge the three dataframes into one by joining on the two NLCD classes
    carbon = carbon_ag_bg_us_df.merge(
        carbon_sd_dd_lt_df, how='outer', on=["StratificationValue", "NLCD1_class", "NLCD2_class"]
    ).merge(carbon_so_df, how='outer', on=["StratificationValue", "NLCD1_class", "NLCD2_class"])

    # unit conversions
    # pixels are in metric tons Carbon per hectare
    # metric tons C / hectare * 1 hectare / 10000 square meters * 900 square meters  = metric tons carbon
    carbon["carbon_ag_bg_us"] = carbon["carbon_ag_bg_us"] / 10000 * 900
    carbon["carbon_sd_dd_lt"] = carbon["carbon_sd_dd_lt"] / 10000 * 900
    carbon["carbon_so"] = carbon["carbon_so"] / 10000 * 900
    return carbon


def calculateFNF(row):
    list = ['Forest to Settlement', 'Forest to Other Land', 'Forest to Cropland', 'Forest to Grassland',
            'Forest to Wetland']
    if row['Category'] in list:
        endClass = row['NLCD_2_ParentClass']
        ag_bg = row['carbon_ag_bg_us'] * carbonStockLoss[str(endClass)]["biomass"]
        sd_dd = row['carbon_sd_dd_lt'] * carbonStockLoss[str(endClass)]["dead organic matter"]
        so = row['carbon_so'] * carbonStockLoss[str(endClass)]["soil organic"]
        result = (ag_bg + sd_dd + so) * 44 / 12
    else:
        result = 0
    return result


def calculate_category(row):
    if row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Forestland':
        return 'Forest Remaining Forest'
    elif row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Settlement':
        return 'Forest to Settlement'
    elif row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Other Land':
        return 'Forest to Other Land'
    elif row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Cropland':
        return 'Forest to Cropland'
    elif row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Grassland':
        return 'Forest to Grassland'
    elif row['NLCD_1_ParentClass'] == 'Forestland' and row['NLCD_2_ParentClass'] == 'Wetland':
        return 'Forest to Wetland'
    elif row['NLCD_1_ParentClass'] != 'Forestland' and row['NLCD_2_ParentClass'] == 'Forestland':
        return 'Nonforest to Forest'
    else:
        return 'Nonforest to Nonforest'


def calculateDisturbances(disturbRast, stratRast, forestAgeRaster, forestAge):
    # calculate disturbances (insects, fires, harvest) by forestAgeRegionType and landuse change type
    # pull the disturbance codes from the dictionary and loop through them
    # map the NLCD class to the parent class in forest age dataframe
    forestAge["NLCD_1_ParentClass"] = forestAge["NLCD1_class"].map(nlcdParentRollupCategories)
    forestAge["NLCD_2_ParentClass"] = forestAge["NLCD2_class"].map(nlcdParentRollupCategories)
    disturbance_cats = list(set(disturbanceLookup.values()))
    for d in disturbance_cats:
        pixValues = [k for k, v in disturbanceLookup.items() if v == d]
        tempDisturbanceDF = tabulateAreaByStratification(
            arcpy.sa.Con(arcpy.sa.InList(disturbRast, pixValues), stratRast, ""),
            forestAgeRaster,
            outputName="ForestAgeTypeRegion",
            colNameArea=d)

        forestAge = forestAge.merge(tempDisturbanceDF, how='outer', on=["StratificationValue", "NLCD1_class",
                                                                        "NLCD2_class", "ForestAgeTypeRegion"])
    return forestAge


def fillNA(forestAge):
    # fill NA with zero to avoid calculation errors
    forestAge['fire_HA'] = forestAge['fire_HA'].fillna(0)
    forestAge['insect_damage_HA'] = forestAge['insect_damage_HA'].fillna(0)
    forestAge['harvest_HA'] = forestAge['harvest_HA'].fillna(0)
    # calculate undisturbed forest arew
    forestAge['undisturbed_HA'] = forestAge['Hectares'] - forestAge['fire_HA'] - forestAge['harvest_HA'] - forestAge[
        'insect_damage_HA']

    # Apply the function to create the "Category" column
    forestAge['Category'] = forestAge.apply(calculate_category, axis=1)
    return forestAge


def mergeAgeFactors(forestAge, forest_lookup_csv):
    # create list of columns to read
    col_list = ['ForestAgeTypeRegion', 'Nonforest to Forest Removal Factor',
                'Forests Remaining Forest Removal Factor', 'Fire Emissions Factor',
                'Insect Emissions Factor', 'Harvest Emissions Factor']
    # read the forest able using column list
    forest_table = pd.read_csv(forest_lookup_csv, usecols=col_list)
    # merge forestAge and forest lookup table
    forestAge = pd.merge(forestAge, forest_table)
    return forestAge


def calculate_FRF(forestAge, year1, year2):
    forestAge['Annual_Removals_Undisturbed_C02'] = (
            (forestAge['undisturbed_HA'] * forestAge['Forests Remaining Forest Removal Factor']) * (44 / 12))
    forestAge['Annual_Removals_N_to_F_C02'] = (
            (forestAge['Hectares'] * forestAge['Nonforest to Forest Removal Factor']) * (44 / 12))
    forestAge['Annual_Emissions_Fire_CO2'] = (
            (forestAge['fire_HA'] * forestAge['Fire Emissions Factor']) * (44 / 12)
            / (int(year2) - int(year1)))
    forestAge['Annual_Emissions_Harvest_CO2'] = (
            (forestAge['harvest_HA'] * forestAge['Harvest Emissions Factor']) * (44 / 12)
            / (int(year2) - int(year1)))
    forestAge['Annual_Emissions_Insect_CO2'] = (
            (forestAge['insect_damage_HA'] * forestAge['Insect Emissions Factor']) * (44 / 12)
            / (int(year2) - int(year1)))


def df2jsonstr(df_dict):
    """
    convert a dictionary of pandas dataframe to a json like string
    :param df_dict: dictionary of pandas data frames
    :return: json like string
    """
    return str({k: v.to_json(orient="records") for (k, v) in df_dict.iteritems()})


def save_results(landuse_result, forestType_result, outputPath, datetime, startTime):
    # build the json like string that contains all the results
    print(
        '{{"stratificationByLanduse": {}, "stratificationByForestAgeRegionType": {}}}'.format(
            landuse_result.to_json(orient="records"),
            forestType_result.to_json(orient="records")
        )
    )

    # save the dataframes as csvs
    strat_csv = os.path.join(outputPath, "stratificationByLanduse.csv")
    strat_forest_csv = os.path.join(outputPath, "stratificationByForestAgeRegionType.csv")

    print(strat_csv)
    print(strat_forest_csv)

    landuse_result.to_csv(strat_csv, index=False)
    forestType_result.to_csv(strat_forest_csv, index=False)

    print("Total processing time: {}".format(datetime.now() - startTime))


def summarize_treecanopy(landuse_result):
    nonforest_df = landuse_result[landuse_result['Category'] == 'Nonforest to Nonforest']

    # Define the columns to sum
    sum_columns = ['Hectares', 'TreeCanopy_HA', 'TreeCanopyLoss_HA']
    if 'Plantable_HA' in landuse_result.columns:
        sum_columns.append('Plantable_HA')

    summary = nonforest_df.groupby('NLCD_2_ParentClass')[sum_columns].sum().reset_index()
    summary["Percent Tree Cover"] = (summary["TreeCanopy_HA"] / summary["Hectares"]) * 100
    if 'Plantable_HA' in summary:
        summary["Percent Plantable"] = (summary["Plantable_HA"] / summary["Hectares"]) * 100

    # Drop 'Hectares' column if it's not needed anymore
    if 'Hectares' in summary.columns:
        summary = summary.drop('Hectares', axis=1)

    # Round all numerical columns
    for column in summary.select_dtypes(include=['float', 'int']):
        summary[column] = summary[column].round().astype(int)

    # Create the sort order
    sort_order = ["Grassland", "Cropland", "Settlement", "Wetland", "Other Land"]

    # Create a categorical type with the specified order
    cat_type = pd.CategoricalDtype(categories=sort_order, ordered=True)

    # Convert the 'LandType' column to this categorical type
    summary['NLCD_2_ParentClass'] = summary['NLCD_2_ParentClass'].astype(cat_type)

    # Now sort the DataFrame using this custom order
    summary = summary.sort_values(by='NLCD_2_ParentClass')

    summary = summary.rename(columns={'NLCD_2_ParentClass': 'Category'})

    return summary


def create_matrix(landuse_result):
    # Define the specific order for the land cover classes
    class_order = [
        'Deciduous Forest', 'Evergreen Forest', 'Mixed Forest', 'Woody Wetlands',
        'Cultivated Crops', 'Hay/Pasture', 'Herbaceous', 'Shrub/Scrub',
        'Open Water', 'Emergent Herbaceous Wetlands', 'Developed, Open Space',
        'Developed, Low Intensity', 'Developed, Medium Intensity',
        'Developed, High Intensity', 'Barren Land', 'Perennial Ice/Snow'
    ]

    # Create the land cover transition matrix
    transition_matrix = landuse_result.pivot_table(index='NLCD1_class', columns='NLCD2_class', values='Hectares',
                                                   aggfunc='sum', fill_value=0)

    # Reorder the matrix to match the specified class order for both rows and columns
    transition_matrix = transition_matrix.reindex(index=class_order, columns=class_order, fill_value=0)

    # Calculate the totals for rows and columns
    transition_matrix['Total'] = transition_matrix.sum(axis=1)
    transition_matrix.loc['Total', :] = transition_matrix.sum()

    # Reset index to make 'NLCD1_class' a column again
    transition_matrix.reset_index(inplace=True)

    # Ensure the column names are correct after resetting the index
    cols = ['NLCD1_class'] + class_order + ['Total']
    transition_matrix = transition_matrix[cols]

    # Round all numerical columns
    for column in transition_matrix.select_dtypes(include=['float', 'int']).columns:
        if column != 'NLCD1_class':  # Skip the land cover class column
            transition_matrix[column] = transition_matrix[column].astype(int)

    return transition_matrix


def calculate_area(category, type, landuse_result, forestType_result):
    if category == "Forest Change" and type == "To Cropland":
        area = int(landuse_result.loc[landuse_result['Category'] == 'Forest to Cropland', 'Hectares'].sum())
    elif category == "Forest Change" and type == "To Grassland":
        area = int(landuse_result.loc[landuse_result['Category'] == 'Forest to Grassland', 'Hectares'].sum())
    elif category == "Forest Change" and type == "To Settlement":
        area = int(landuse_result.loc[landuse_result['Category'] == 'Forest to Settlement', 'Hectares'].sum())
    elif category == "Forest Change" and type == "To Wetland":
        area = int(landuse_result.loc[landuse_result['Category'] == 'Forest to Wetland', 'Hectares'].sum())
    elif category == "Forest Change" and type == "To Other":
        area = int(landuse_result.loc[landuse_result['Category'] == 'Forest to Other Land', 'Hectares'].sum())
    elif category == "Forest Change" and type == "Reforestation (Non-Forest to Forest)":
        area = int(forestType_result.loc[forestType_result['Category'] == 'Nonforest to Forest', 'Hectares'].sum())
    elif category == "Forest Remaining Forest" and type == "Undisturbed":
        area = int(forestType_result.loc[forestType_result['Category'] == 'Forest Remaining Forest', 'undisturbed_HA'].sum())
    elif category == "Forest Remaining Forest" and type == "Fire":
        area = int(forestType_result.loc[forestType_result['Category'] == 'Forest Remaining Forest', 'fire_HA'].sum())
    elif category == "Forest Remaining Forest" and type == "Insect/Disease":
        area = int(forestType_result.loc[forestType_result['Category'] == 'Forest Remaining Forest', 'insect_damage_HA'].sum())
    elif category == "Forest Remaining Forest" and type == "Harvest/Other":
        area = int(forestType_result.loc[forestType_result['Category'] == 'Forest Remaining Forest', 'harvest_HA'].sum())
    else:
        area = 0  # or "Error" if appropriate
    return area



def calculate_ghg_flux(category, type, landuse_result, forestType_result, years):
    if category == "Forest Change" and type == "To Cropland":
        ghg = int(
            (
                landuse_result.loc[
                    landuse_result['Category'] == 'Forest to Cropland',
                    'Total Emissions Forest to Non Forest CO2'
                ].sum()
            ) / years
        )
    elif category == "Forest Change" and type == "To Grassland":
        ghg = int(
            (
                landuse_result.loc[
                    landuse_result['Category'] == 'Forest to Grassland',
                    'Total Emissions Forest to Non Forest CO2'
                ].sum()
            ) / years
        )
    elif category == "Forest Change" and type == "To Settlement":
        ghg = int(
            (
                landuse_result.loc[
                    landuse_result['Category'] == 'Forest to Settlement',
                    'Total Emissions Forest to Non Forest CO2'
                ].sum()
            ) / years
        )
    elif category == "Forest Change" and type == "To Wetland":
        ghg = int(
            (
                landuse_result.loc[
                    landuse_result['Category'] == 'Forest to Wetland',
                    'Total Emissions Forest to Non Forest CO2'
                ].sum()
            ) / years
        )
    elif category == "Forest Change" and type == "To Other":
        ghg = int(
            (
                landuse_result.loc[
                    landuse_result['Category'] == 'Forest to Other Land',
                    'Total Emissions Forest to Non Forest CO2'
                ].sum()
            ) / years
        )
    elif category == "Forest Change" and type == "Reforestation (Non-Forest to Forest)":
        ghg = int(
            forestType_result.loc[
                forestType_result['Category'] == 'Nonforest to Forest',
                'Annual_Removals_N_to_F_C02'
            ].sum()
        )
    elif category == "Forest Remaining Forest" and type == "Undisturbed":
        ghg = int(
            forestType_result.loc[
                forestType_result['Category'] == 'Forest Remaining Forest',
                'Annual_Removals_Undisturbed_C02'
            ].sum()
        )
    elif category == "Forest Remaining Forest" and type == "Fire":
        ghg = int(
            forestType_result.loc[
                forestType_result['Category'] == 'Forest Remaining Forest',
                'Annual_Emissions_Fire_CO2'
            ].sum()
        )
    elif category == "Forest Remaining Forest" and type == "Insect/Disease":
        ghg = int(
            forestType_result.loc[
                forestType_result['Category'] == 'Forest Remaining Forest',
                'Annual_Emissions_Insect_CO2'
            ].sum()
        )
    elif category == "Forest Remaining Forest" and type == "Harvest/Other":
        ghg = int(
            forestType_result.loc[
                forestType_result['Category'] == 'Forest Remaining Forest',
                'Annual_Emissions_Harvest_CO2'
            ].sum()
        )
    else:
        ghg = 0  # Set to 0 if the category and type do not match any conditions
    return ghg


def summarize_ghg(landuse_result, forestType_result, years):
    ghg_result = pd.DataFrame()
    ghg_result["Category"] = [
        'Forest Change', 'Forest Change', 'Forest Change', 'Forest Change', 'Forest Change',
        'Forest Change', 'Forest Remaining Forest', 'Forest Remaining Forest',
        'Forest Remaining Forest', 'Forest Remaining Forest'
    ]
    ghg_result["Type"] = [
        'To Cropland', 'To Grassland', 'To Settlement', 'To Wetland', 'To Other',
        'Reforestation (Non-Forest to Forest)', 'Undisturbed', 'Fire',
        'Insect/Disease', 'Harvest/Other'
    ]
    ghg_result["Emissions/Removals"] = [
        'Emissions', 'Emissions', 'Emissions', 'Emissions', 'Emissions', 'Removals',
        'Removals', 'Emissions', 'Emissions', 'Emissions'
    ]
    # Add calculations for 'Area (ha, total)' and 'GHG Flux (t CO2e/year)'
    ghg_result['Area (ha, total)'] = [
        calculate_area(cat, typ, landuse_result, forestType_result)
        for cat, typ in zip(ghg_result['Category'], ghg_result['Type'])
    ]
    ghg_result['GHG Flux (t CO2e/year)'] = [
        calculate_ghg_flux(cat, typ, landuse_result, forestType_result, years)
        for cat, typ in zip(ghg_result['Category'], ghg_result['Type'])
    ]

    return ghg_result



def write_dataframes_to_csv(df_list, csv_file_path, space=5):
    with open(csv_file_path, 'w', newline='') as f:
        for i, df in enumerate(df_list):
            # Write the DataFrame to the CSV file
            df.to_csv(f, index=False)
            # If this is not the last DataFrame, add the specified number of empty rows
            if i < len(df_list) - 1:
                f.write('\n' * space)

