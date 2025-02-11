# funcs.py

"""
Helper functions used across multiple parts of the GP tool for land use change analysis.

This module includes functions for data conversion, raster processing,
data summarization, and calculation of emissions and removals.

Author: [Your Name]
Date: [Current Date]
"""

import os
from datetime import datetime as dt
import arcpy
import pandas as pd
import numpy as np
from arcpy.sa import (
    TabulateArea,
    ZonalStatisticsAsTable,
    Con,
    Raster,
    CellStatistics,
    InList,
    ExtractByMask,
)
from lookups import (
    nlcdCategories,
    nlcdParentRollupCategories,
    disturbanceLookup,
    carbonStockLoss,
)

# Ensure overwriting of outputs
arcpy.env.overwriteOutput = True

# Check out the Spatial Analyst extension
if arcpy.CheckExtension("Spatial") == "Available":
    arcpy.CheckOutExtension("Spatial")
else:
    raise RuntimeError("Spatial Analyst extension is not available.")


def feature_class_to_pandas_dataframe(
    feature_class: str, field_list: list
) -> pd.DataFrame:
    """
    Load data from an ArcGIS Feature Class into a Pandas DataFrame.

    Args:
        feature_class (str): Path to the input ArcGIS Feature Class.
        field_list (list): List of field names to include.

    Returns:
        pd.DataFrame: DataFrame containing the requested data.
    """
    array = arcpy.da.FeatureClassToNumPyArray(
        in_table=feature_class,
        field_names=field_list,
        skip_nulls=False,
        null_value=-99999,
    )
    return pd.DataFrame(data=array)


def create_landuse_stratification_raster(
    nlcd_raster1: str, nlcd_raster2: str, aoi: str
) -> Raster:
    """
    Create a stratification raster to track land use changes between two NLCD rasters.

    Each pixel value combines the land use classes from both years,
    allowing for detailed change analysis.

    Args:
        nlcd_raster1 (str): Path to the initial NLCD raster.
        nlcd_raster2 (str): Path to the subsequent NLCD raster.
        aoi (str): Path to the Area of Interest polygon feature.

    Returns:
        arcpy.sa.Raster: Stratification raster.
    """
    # Clip the NLCD rasters to the AOI
    arcpy.management.Clip(nlcd_raster1, "#", "in_memory/nlcd_before", aoi, "", "ClippingGeometry")
    arcpy.management.Clip(nlcd_raster2, "#", "in_memory/nlcd_after", aoi, "", "ClippingGeometry")

    # Create the stratification raster
    strat_raster = Raster("in_memory/nlcd_before") * 100 + Raster("in_memory/nlcd_after")

    return strat_raster


def rollup_to_parent_class(
    df: pd.DataFrame, columns_to_aggregate: list, group_by: list = None
) -> pd.DataFrame:
    """
    Roll up values to parent NLCD categories using sum aggregation.

    Args:
        df (pd.DataFrame): Input DataFrame.
        columns_to_aggregate (list): List of columns to sum.
        group_by (list, optional): Additional columns to group by.

    Returns:
        pd.DataFrame: DataFrame with values summed by parent categories.
    """
    df_copy = df.copy()

    # Map NLCD classes to parent classes
    df_copy["NLCD1_parentclass"] = df_copy["NLCD1_class"].map(nlcdParentRollupCategories)
    df_copy["NLCD2_parentclass"] = df_copy["NLCD2_class"].map(nlcdParentRollupCategories)

    group_columns = ["NLCD1_parentclass", "NLCD2_parentclass"]
    if group_by is not None:
        group_columns += group_by

    grouped_df = df_copy.groupby(group_columns)[columns_to_aggregate].sum().reset_index()

    return grouped_df


def pivot_dataframe(
    df: pd.DataFrame, values: str, index_category: str, column_category: str
) -> pd.DataFrame:
    """
    Create a pivot table similar to Excel's pivot functionality.

    Args:
        df (pd.DataFrame): Input DataFrame.
        values (str): Column to aggregate.
        index_category (str): Column to use as rows.
        column_category (str): Column to use as columns.

    Returns:
        pd.DataFrame: Pivoted DataFrame.
    """
    pivot_df = pd.pivot_table(
        df, values=values, index=[index_category], columns=[column_category], aggfunc=np.sum
    )
    return pivot_df


def tabulate_area_by_stratification(
    stratification_raster: Raster,
    value_raster: str,
    output_name: str,
    pixel_size: int = 30,
    area_column_name: str = "Hectares",
) -> pd.DataFrame:
    """
    Tabulate the area of different classes within a value raster for each stratification class.

    Args:
        stratification_raster (Raster): Stratification raster.
        value_raster (str): Path to the raster containing values to tabulate.
        output_name (str): Name for the output value column.
        pixel_size (int, optional): Pixel size in meters. Defaults to 30.
        area_column_name (str, optional): Name for the area column. Defaults to "Hectares".

    Returns:
        pd.DataFrame: DataFrame with area calculations.
    """
    # Perform tabulate area analysis
    cross_tab = TabulateArea(
        stratification_raster,
        "Value",
        value_raster,
        "Value",
        "in_memory/cross_tab",
        pixel_size,
    )

    # Convert results to DataFrame
    cross_tab_df = feature_class_to_pandas_dataframe(cross_tab, "*")

    # Reshape data from wide to long format
    df_melted = pd.melt(
        cross_tab_df, id_vars=["OBJECTID", "VALUE"], value_name="Area"
    )

    # Extract raster value from variable names
    df_melted["Raster_VALUE"] = df_melted["variable"].str.extract(r"VALUE_(\d+)").astype(int)

    # Unpack NLCD codes from the stratification raster
    df_melted["NLCD1_value"] = df_melted["VALUE"] // 100
    df_melted["NLCD2_value"] = df_melted["VALUE"] % 100

    # Map NLCD values to categories
    df_melted["NLCD1_class"] = df_melted["NLCD1_value"].map(nlcdCategories)
    df_melted["NLCD2_class"] = df_melted["NLCD2_value"].map(nlcdCategories)

    # Filter out rows with zero area and create a copy
    df_filtered = df_melted[df_melted["Area"] > 0].copy()

    # Calculate area in hectares
    df_filtered[area_column_name] = df_filtered["Area"] / 10000  # 1 hectare = 10,000 m²

    # Rename columns for clarity
    df_filtered = df_filtered.rename(
        columns={"Raster_VALUE": output_name, "VALUE": "StratificationValue"}
    )

    # Clean up in-memory workspace
    arcpy.management.Delete("in_memory/cross_tab")

    return df_filtered[
        ["StratificationValue", "NLCD1_class", "NLCD2_class", output_name, area_column_name]
    ]


def zonal_sum_by_stratification(
    stratification_raster: Raster,
    value_raster: str,
    column_name: str,
    cell_size: int = 30,
) -> pd.DataFrame:
    """
    Calculate the sum of values within a raster for each stratification class.

    Args:
        stratification_raster (Raster): Stratification raster.
        value_raster (str): Path to the raster containing values to sum.
        column_name (str): Name for the summed values column.
        cell_size (int, optional): Cell size in meters. Defaults to 30.

    Returns:
        pd.DataFrame: DataFrame with summed values.
    """
    # Perform zonal statistics
    zonal_stats = ZonalStatisticsAsTable(
        stratification_raster,
        "Value",
        value_raster,
        "in_memory/zonal_stats",
        statistics_type="SUM",
    )

    # Convert to DataFrame
    zs_df = feature_class_to_pandas_dataframe(zonal_stats, ["VALUE", "COUNT", "SUM"])

    # Rename columns for clarity
    zs_df.columns = ["StratificationValue", "CellCount", column_name]

    # Unpack NLCD codes
    zs_df["NLCD1_value"] = zs_df["StratificationValue"] // 100
    zs_df["NLCD2_value"] = zs_df["StratificationValue"] % 100
    zs_df["NLCD1_class"] = zs_df["NLCD1_value"].map(nlcdCategories)
    zs_df["NLCD2_class"] = zs_df["NLCD2_value"].map(nlcdCategories)

    # Calculate area in hectares
    zs_df["Hectares"] = (zs_df["CellCount"] * cell_size ** 2) / 10000

    # Clean up in-memory workspace
    arcpy.management.Delete("in_memory/zonal_stats")

    return zs_df[
        ["StratificationValue", "NLCD1_class", "NLCD2_class", "Hectares", "CellCount", column_name]
    ]


def calculate_tree_canopy(
    tree_canopy_1: str,
    tree_canopy_2: str,
    strat_raster: Raster,
    tree_canopy_source: str,
    aoi: str,
    cell_size: int,
) -> pd.DataFrame:
    """
    Calculate tree canopy averages and losses by stratification class.

    Args:
        tree_canopy_1 (str): Path to tree canopy raster for the first year.
        tree_canopy_2 (str): Path to tree canopy raster for the second year.
        strat_raster (Raster): Stratification raster.
        tree_canopy_source (str): Identifier for the tree canopy data source.
        aoi (str): Path to the Area of Interest polygon feature.
        cell_size (int): Cell size in meters.

    Returns:
        pd.DataFrame: DataFrame with tree canopy metrics.
    """
    if not tree_canopy_1 or not tree_canopy_2:
        arcpy.AddMessage("Skipping Tree Canopy - no data provided.")
        return pd.DataFrame(
            columns=[
                "StratificationValue",
                "NLCD1_class",
                "NLCD2_class",
                "TreeCanopy_HA",
                "TreeCanopyLoss_HA",
            ]
        )

    # Calculate average tree canopy
    tree_canopy_avg = (Raster(tree_canopy_1) + Raster(tree_canopy_2)) / 2

    # Calculate tree canopy loss
    tree_canopy_diff = Con(
        Raster(tree_canopy_2) < Raster(tree_canopy_1),
        Raster(tree_canopy_1) - Raster(tree_canopy_2),
        0,
    )

    # Apply AOI mask using ExtractByMask
    arcpy.AddMessage("Applying AOI mask using ExtractByMask.")
    tree_canopy_avg_masked = ExtractByMask(tree_canopy_avg, aoi)
    tree_canopy_diff_masked = ExtractByMask(tree_canopy_diff, aoi)

    # Zonal sum for average and loss
    tc_avg_df = zonal_sum_by_stratification(strat_raster, tree_canopy_avg_masked, "TreeCanopy_HA", cell_size)
    tc_diff_df = zonal_sum_by_stratification(strat_raster, tree_canopy_diff_masked, "TreeCanopyLoss_HA", cell_size)

    # Merge dataframes
    tree_cover = tc_avg_df.merge(
        tc_diff_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )

    # Unit conversion based on data source
    if "NLCD" in tree_canopy_source:
        # For NLCD data
        # Convert percentage to fraction, multiply by cell area (cell_size^2), convert to hectares
        factor = 0.01 * (cell_size ** 2) / 10000
    elif "CBW" in tree_canopy_source:
        # For CBW data
        factor = (cell_size ** 2) / 10000
    else:
        # Default conversion factor
        factor = (cell_size ** 2) / 10000

    tree_cover["TreeCanopy_HA"] *= factor
    tree_cover["TreeCanopyLoss_HA"] *= factor

    # Drop unnecessary columns
    tree_cover.drop(["Hectares", "CellCount"], axis=1, inplace=True)

    return tree_cover


def calculate_plantable_areas(
    plantable_areas_raster: str,
    strat_raster: Raster,
    tree_cover_df: pd.DataFrame,
    aoi: str,
    cell_size: int,
) -> pd.DataFrame:
    """
    Calculate plantable areas by stratification class.

    Args:
        plantable_areas_raster (str): Path to the plantable areas raster.
        strat_raster (Raster): Stratification raster.
        tree_cover_df (pd.DataFrame): DataFrame with tree canopy data.
        aoi (str): Path to the Area of Interest polygon feature.
        cell_size (int): Cell size in meters.

    Returns:
        pd.DataFrame: Updated DataFrame with plantable areas.
    """
    if plantable_areas_raster.lower() == "none":
        arcpy.AddMessage("Skipping Plantable Areas - no data provided.")
        return tree_cover_df

    # Apply AOI mask using ExtractByMask
    arcpy.AddMessage("Applying AOI mask to plantable areas raster.")
    plantable_raster_masked = ExtractByMask(plantable_areas_raster, aoi)

    # Zonal sum for plantable areas
    plantable_sum_df = zonal_sum_by_stratification(strat_raster, plantable_raster_masked, "Plantable_HA", cell_size)

    # Merge with tree cover DataFrame
    tree_cover_df = tree_cover_df.merge(
        plantable_sum_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )

    # Convert to hectares
    factor = (cell_size ** 2) / 10000
    tree_cover_df["Plantable_HA"] *= factor

    return tree_cover_df


def compute_disturbance_max(
    disturbance_rasters: list, strat_raster: Raster
) -> tuple:
    """
    Calculate the maximum disturbance class per pixel and tabulate area.

    Args:
        disturbance_rasters (list): List of paths to disturbance rasters.
        strat_raster (Raster): Stratification raster.

    Returns:
        tuple: DataFrame with disturbance areas and the combined disturbance raster.
    """
    if len(disturbance_rasters) == 1:
        disturb_raster = disturbance_rasters[0]
    else:
        disturb_raster = CellStatistics(disturbance_rasters, "MAXIMUM", ignore_nodata="DATA")

    disturbance_df = tabulate_area_by_stratification(
        strat_raster, disturb_raster, output_name="Disturbance"
    )

    # Map disturbance codes to classes
    disturbance_df["DisturbanceClass"] = disturbance_df["Disturbance"].map(disturbanceLookup)

    # Pivot the DataFrame to wide format
    disturbance_wide = disturbance_df.pivot_table(
        index=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        columns="DisturbanceClass",
        values="Hectares",
        aggfunc="sum",
    ).reset_index()

    # Ensure necessary columns exist
    required_columns = ["fire_HA", "harvest_HA", "insect_damage_HA"]
    for col in required_columns:
        if col not in disturbance_wide.columns:
            disturbance_wide[col] = 0

    return disturbance_wide, disturb_raster


def zonal_sum_carbon(
    strat_raster: Raster,
    carbon_ag_bg_us: str,
    carbon_sd_dd_lt: str,
    carbon_so: str,
    cell_size: int = 30,
) -> pd.DataFrame:
    """
    Calculate the sum of carbon stocks by stratification class.

    Args:
        strat_raster (Raster): Stratification raster.
        carbon_ag_bg_us (str): Path to above and below ground carbon raster.
        carbon_sd_dd_lt (str): Path to standing dead, down dead, and litter carbon raster.
        carbon_so (str): Path to soil organic carbon raster.
        cell_size (int, optional): Cell size in meters. Defaults to 30.

    Returns:
        pd.DataFrame: DataFrame with summed carbon stocks.
    """
    # Calculate zonal sums for each carbon component
    carbon_ag_bg_us_df = zonal_sum_by_stratification(strat_raster, carbon_ag_bg_us, "carbon_ag_bg_us", cell_size)
    carbon_sd_dd_lt_df = zonal_sum_by_stratification(strat_raster, carbon_sd_dd_lt, "carbon_sd_dd_lt", cell_size)
    carbon_so_df = zonal_sum_by_stratification(strat_raster, carbon_so, "carbon_so", cell_size)

    # Merge DataFrames
    carbon_df = carbon_ag_bg_us_df.merge(
        carbon_sd_dd_lt_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    ).merge(
        carbon_so_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )

    # Unit conversion to metric tons of carbon
    # Pixels are in metric tons C per hectare
    # Convert per hectare values to per pixel values
    # (metric tons C per hectare) * (cell area in hectares) = metric tons C per pixel
    cell_area_ha = (cell_size ** 2) / 10000  # Convert cell area from m² to hectares
    carbon_df["carbon_ag_bg_us"] *= cell_area_ha
    carbon_df["carbon_sd_dd_lt"] *= cell_area_ha
    carbon_df["carbon_so"] *= cell_area_ha

    return carbon_df


def calculate_forest_to_nonforest_emissions(row: pd.Series, years_diff: int) -> float:
    """
    Calculate *annual* emissions (t CO2/yr) from forest to non-forest changes,
    dividing total flux by 'years_diff'.

    Args:
        row (pd.Series): Row from landuse_df, including carbon stocks, category, etc.
        years_diff (int): The number of years between year1 and year2
                          (e.g. 3 for 2013-2016).

    Returns:
        float: Annual forest-to-nonforest emissions (t CO2/yr).
    """
    categories = [
        "Forest to Settlement",
        "Forest to Other Land",
        "Forest to Cropland",
        "Forest to Grassland",
        "Forest to Wetland",
    ]
    if row["Category"] in categories:
        end_class = row["NLCD_2_ParentClass"]
        ag_bg = row["carbon_ag_bg_us"] * carbonStockLoss[end_class]["biomass"]
        sd_dd = row["carbon_sd_dd_lt"] * carbonStockLoss[end_class]["dead organic matter"]
        so = row["carbon_so"] * carbonStockLoss[end_class]["soil organic"]

        total_c_loss = ag_bg + sd_dd + so  # total carbon lost
        total_co2_loss = total_c_loss * (44 / 12)  # convert C to CO2

        # Now divide by years_diff to get an ANNUAL rate (t CO2/yr)
        return total_co2_loss / years_diff
    else:
        return 0.0


def determine_landuse_category(row: pd.Series) -> str:
    """
    Determine the land use change category based on NLCD parent classes.

    Args:
        row (pd.Series): Row from a DataFrame.

    Returns:
        str: Land use change category.
    """
    if row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Forestland":
        return "Forest Remaining Forest"
    elif row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Settlement":
        return "Forest to Settlement"
    elif row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Other Land":
        return "Forest to Other Land"
    elif row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Cropland":
        return "Forest to Cropland"
    elif row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Grassland":
        return "Forest to Grassland"
    elif row["NLCD_1_ParentClass"] == "Forestland" and row["NLCD_2_ParentClass"] == "Wetland":
        return "Forest to Wetland"
    elif row["NLCD_1_ParentClass"] != "Forestland" and row["NLCD_2_ParentClass"] == "Forestland":
        return "Nonforest to Forest"
    else:
        return "Nonforest to Nonforest"


def calculate_disturbances(
    disturbance_raster: Raster,
    strat_raster: Raster,
    forest_age_raster: str,
    forest_age_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculate disturbances (fire, insect damage, harvest) by forest age type and land use change.

    Args:
        disturbance_raster (Raster): Combined disturbance raster.
        strat_raster (Raster): Stratification raster.
        forest_age_raster (str): Path to the forest age raster.
        forest_age_df (pd.DataFrame): DataFrame with forest age data.

    Returns:
        pd.DataFrame: Updated DataFrame with disturbance areas.
    """
    # Map NLCD classes to parent classes
    forest_age_df["NLCD_1_ParentClass"] = forest_age_df["NLCD1_class"].map(nlcdParentRollupCategories)
    forest_age_df["NLCD_2_ParentClass"] = forest_age_df["NLCD2_class"].map(nlcdParentRollupCategories)

    disturbance_categories = set(disturbanceLookup.values())

    for disturbance in disturbance_categories:
        pixel_values = [k for k, v in disturbanceLookup.items() if v == disturbance]

        # Use conditional raster to isolate disturbance pixels
        disturbance_condition = Con(
            InList(disturbance_raster, pixel_values), strat_raster, ""
        )

        temp_df = tabulate_area_by_stratification(
            disturbance_condition,
            forest_age_raster,
            output_name="ForestAgeTypeRegion",
            area_column_name=disturbance,
        )

        # Merge disturbance data
        forest_age_df = forest_age_df.merge(
            temp_df,
            on=["StratificationValue", "NLCD1_class", "NLCD2_class", "ForestAgeTypeRegion"],
            how="outer",
        )

    return forest_age_df


def fill_na_values(forest_age_df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill NA values with zeros and calculate undisturbed forest area.

    Args:
        forest_age_df (pd.DataFrame): DataFrame with forest age and disturbance data.

    Returns:
        pd.DataFrame: Updated DataFrame.
    """
    # Fill NA values
    forest_age_df["fire_HA"] = forest_age_df["fire_HA"].fillna(0)
    forest_age_df["insect_damage_HA"] = forest_age_df["insect_damage_HA"].fillna(0)
    forest_age_df["harvest_HA"] = forest_age_df["harvest_HA"].fillna(0)

    # Calculate undisturbed area
    forest_age_df["undisturbed_HA"] = (
        forest_age_df["Hectares"]
        - forest_age_df["fire_HA"]
        - forest_age_df["harvest_HA"]
        - forest_age_df["insect_damage_HA"]
    )

    # Determine land use category
    forest_age_df["Category"] = forest_age_df.apply(determine_landuse_category, axis=1)

    return forest_age_df


def merge_age_factors(
    forest_age_df: pd.DataFrame, forest_lookup_csv: str
) -> pd.DataFrame:
    """
    Merge forest age DataFrame with lookup table for emission and removal factors.

    Args:
        forest_age_df (pd.DataFrame): DataFrame with forest age data.
        forest_lookup_csv (str): Path to the CSV file containing lookup data.

    Returns:
        pd.DataFrame: Merged DataFrame.
    """
    columns_to_use = [
        "ForestAgeTypeRegion",
        "Nonforest to Forest Removal Factor",
        "Forests Remaining Forest Removal Factor",
        "Fire Emissions Factor",
        "Insect Emissions Factor",
        "Harvest Emissions Factor",
    ]
    forest_table = pd.read_csv(forest_lookup_csv, usecols=columns_to_use)

    # Merge data
    merged_df = forest_age_df.merge(forest_table, on="ForestAgeTypeRegion", how="left")

    return merged_df


def calculate_forest_removals_and_emissions(
    forest_age_df: pd.DataFrame, year1: int, year2: int
) -> pd.DataFrame:
    """
    Calculate annual emissions and removals from forests.

    Args:
        forest_age_df (pd.DataFrame): DataFrame with forest age and disturbance data.
        year1 (int): Starting year.
        year2 (int): Ending year.

    Returns:
        pd.DataFrame: DataFrame with calculated emissions and removals.
    """
    years_difference = year2 - year1

    # Removals from undisturbed forests
    forest_age_df["Annual_Removals_Undisturbed_CO2"] = (
        forest_age_df["undisturbed_HA"] * forest_age_df["Forests Remaining Forest Removal Factor"] * (44 / 12)
    )

    # Removals from non-forest to forest
    forest_age_df["Annual_Removals_N_to_F_CO2"] = (
        forest_age_df["Hectares"] * forest_age_df["Nonforest to Forest Removal Factor"] * (44 / 12)
    )

    # Emissions from disturbances
    forest_age_df["Annual_Emissions_Fire_CO2"] = (
        forest_age_df["fire_HA"] * forest_age_df["Fire Emissions Factor"] * (44 / 12) / years_difference
    )
    forest_age_df["Annual_Emissions_Harvest_CO2"] = (
        forest_age_df["harvest_HA"] * forest_age_df["Harvest Emissions Factor"] * (44 / 12) / years_difference
    )
    forest_age_df["Annual_Emissions_Insect_CO2"] = (
        forest_age_df["insect_damage_HA"] * forest_age_df["Insect Emissions Factor"] * (44 / 12) / years_difference
    )

    return forest_age_df


def summarize_tree_canopy(landuse_df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarize tree canopy data for non-forest to non-forest transitions.

    Args:
        landuse_df (pd.DataFrame): DataFrame with land use data.

    Returns:
        pd.DataFrame: Summary DataFrame.
    """
    nonforest_df = landuse_df[landuse_df["Category"] == "Nonforest to Nonforest"]

    # Define columns to sum
    sum_columns = ["Hectares", "TreeCanopy_HA", "TreeCanopyLoss_HA"]
    if "Plantable_HA" in landuse_df.columns:
        sum_columns.append("Plantable_HA")

    summary = nonforest_df.groupby("NLCD_2_ParentClass")[sum_columns].sum().reset_index()

    # Calculate percentages
    summary["Percent Tree Cover"] = (summary["TreeCanopy_HA"] / summary["Hectares"]) * 100
    if "Plantable_HA" in summary.columns:
        summary["Percent Plantable"] = (summary["Plantable_HA"] / summary["Hectares"]) * 100

    # Drop unnecessary columns
    summary.drop("Hectares", axis=1, inplace=True)

    # Round numerical columns
    numerical_cols = summary.select_dtypes(include=["float", "int"]).columns
    summary[numerical_cols] = summary[numerical_cols].round().astype(int)

    # Sort the DataFrame
    sort_order = ["Grassland", "Cropland", "Settlement", "Wetland", "Other Land"]
    summary["NLCD_2_ParentClass"] = pd.Categorical(summary["NLCD_2_ParentClass"], categories=sort_order, ordered=True)
    summary.sort_values("NLCD_2_ParentClass", inplace=True)

    # Rename column for clarity
    summary.rename(columns={"NLCD_2_ParentClass": "Category"}, inplace=True)

    return summary


def create_land_cover_transition_matrix(landuse_df: pd.DataFrame) -> pd.DataFrame:
    """
    Create a land cover transition matrix.

    Args:
        landuse_df (pd.DataFrame): DataFrame with land use data.

    Returns:
        pd.DataFrame: Transition matrix DataFrame.
    """
    class_order = [
        "Deciduous Forest",
        "Evergreen Forest",
        "Mixed Forest",
        "Woody Wetlands",
        "Cultivated Crops",
        "Hay/Pasture",
        "Herbaceous",
        "Shrub/Scrub",
        "Open Water",
        "Emergent Herbaceous Wetlands",
        "Developed, Open Space",
        "Developed, Low Intensity",
        "Developed, Medium Intensity",
        "Developed, High Intensity",
        "Barren Land",
        "Perennial Ice/Snow",
    ]

    # Create pivot table
    transition_matrix = landuse_df.pivot_table(
        index="NLCD1_class",
        columns="NLCD2_class",
        values="Hectares",
        aggfunc="sum",
        fill_value=0,
    )

    # Reorder rows and columns
    transition_matrix = transition_matrix.reindex(index=class_order, columns=class_order, fill_value=0)

    # Calculate totals
    transition_matrix["Total"] = transition_matrix.sum(axis=1)
    transition_matrix.loc["Total"] = transition_matrix.sum()

    # Reset index
    transition_matrix.reset_index(inplace=True)

    # Round numerical columns
    numerical_cols = transition_matrix.select_dtypes(include=["float", "int"]).columns
    for col in numerical_cols:
        if col != "NLCD1_class":
            transition_matrix[col] = transition_matrix[col].astype(int)

    return transition_matrix


def calculate_area(
    category: str, type_: str, landuse_df: pd.DataFrame, forest_type_df: pd.DataFrame
) -> int:
    """
    Calculate total area for a specific category and type.

    Args:
        category (str): Main category.
        type_ (str): Specific type within the category.
        landuse_df (pd.DataFrame): Land use DataFrame.
        forest_type_df (pd.DataFrame): Forest type DataFrame.

    Returns:
        int: Total area in hectares.
    """
    if category == "Forest Change":
        mapping = {
            "To Cropland": "Forest to Cropland",
            "To Grassland": "Forest to Grassland",
            "To Settlement": "Forest to Settlement",
            "To Wetland": "Forest to Wetland",
            "To Other": "Forest to Other Land",
            "Reforestation (Non-Forest to Forest)": "Nonforest to Forest",
        }
        key = mapping.get(type_)
        if key:
            if type_ == "Reforestation (Non-Forest to Forest)":
                area = forest_type_df.loc[forest_type_df["Category"] == key, "Hectares"].sum()
            else:
                area = landuse_df.loc[landuse_df["Category"] == key, "Hectares"].sum()
            return int(area)

    elif category == "Forest Remaining Forest":
        columns_mapping = {
            "Undisturbed": "undisturbed_HA",
            "Fire": "fire_HA",
            "Insect/Disease": "insect_damage_HA",
            "Harvest/Other": "harvest_HA",
        }
        column = columns_mapping.get(type_)
        if column:
            # Include both 'Forest Remaining Forest' and 'Forest Remaining Forest (fire)'
            subset = forest_type_df["Category"].isin(["Forest Remaining Forest", "Forest Remaining Forest (fire)"])
            area = forest_type_df.loc[subset, column].sum()
            return int(area)

    elif category == "Trees Outside Forest":
        if type_ == "Tree canopy loss" and "TreeCanopyLoss_HA" in landuse_df.columns:
            area = landuse_df.loc[
                landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopyLoss_HA"
            ].sum()
            return int(area)
        elif type_ == "Canopy maintained/gained" and "TreeCanopy_HA" in landuse_df.columns:
            area = landuse_df.loc[
                landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopy_HA"
            ].sum()
            return int(area)
        else:
            return 0
    return 0


def calculate_ghg_flux(
    category: str,
    type_: str,
    landuse_df: pd.DataFrame,
    forest_type_df: pd.DataFrame,
    years: int,
    emissions_factor: float = None,
    removals_factor: float = None,
    c_to_co2: float = 44 / 12,
) -> int:
    """
    Calculate GHG flux for a specific category and type (in t CO2e/yr).

    Args:
        category (str): Main category (e.g., "Forest Change").
        type_ (str): Specific type (e.g., "To Settlement").
        landuse_df (pd.DataFrame): Land use DataFrame (already annual for forest-to-nonforest).
        forest_type_df (pd.DataFrame): Forest type DataFrame (already annual for disturbances).
        years (int): Number of years between observations (no longer used for forest-to-nonforest).
        emissions_factor (float, optional): Emission factor for trees outside forests.
        removals_factor (float, optional): Removal factor for trees outside forests.
        c_to_co2 (float, optional): Conversion factor from carbon to CO2.

    Returns:
        int: GHG flux in metric tons of CO2e per year (rounded to int).
    """
    if category == "Forest Change":
        mapping = {
            "To Cropland": "Forest to Cropland",
            "To Grassland": "Forest to Grassland",
            "To Settlement": "Forest to Settlement",
            "To Wetland": "Forest to Wetland",
            "To Other": "Forest to Other Land",
            "Reforestation (Non-Forest to Forest)": "Nonforest to Forest",
        }
        key = mapping.get(type_)
        if key:
            if type_ == "Reforestation (Non-Forest to Forest)":
                ghg = forest_type_df.loc[
                    forest_type_df["Category"] == key,
                    "Annual_Removals_N_to_F_CO2"
                ].sum()
            else:
                ghg = landuse_df.loc[
                    landuse_df["Category"] == key,
                    "Annual Emissions Forest to Non Forest CO2"
                ].sum()
            return int(ghg)

    elif category == "Forest Remaining Forest":
        columns_mapping = {
            "Undisturbed": "Annual_Removals_Undisturbed_CO2",
            "Fire": "Annual_Emissions_Fire_CO2",
            "Insect/Disease": "Annual_Emissions_Insect_CO2",
            "Harvest/Other": "Annual_Emissions_Harvest_CO2",
        }
        column = columns_mapping.get(type_)
        if column:
            # Include both 'Forest Remaining Forest' and 'Forest Remaining Forest (fire)'
            subset = forest_type_df["Category"].isin(["Forest Remaining Forest", "Forest Remaining Forest (fire)"])
            ghg = forest_type_df.loc[subset, column].sum()
            return int(ghg)

    elif category == "Trees Outside Forest":
        if type_ == "Tree canopy loss" and "TreeCanopyLoss_HA" in landuse_df.columns:
            if emissions_factor is None:
                raise ValueError("Emissions factor is required for tree canopy loss emissions calculation.")
            area = landuse_df.loc[
                landuse_df["Category"] == "Nonforest to Nonforest",
                "TreeCanopyLoss_HA"
            ].sum()
            # Possibly keep dividing by years if you want it to be annual
            ghg = (area * emissions_factor * c_to_co2) / years
            return int(ghg)
        elif type_ == "Canopy maintained/gained" and "TreeCanopy_HA" in landuse_df.columns:
            if removals_factor is None:
                raise ValueError("Removals factor is required for canopy maintained/gained removals calculation.")
            area = landuse_df.loc[
                landuse_df["Category"] == "Nonforest to Nonforest",
                "TreeCanopy_HA"
            ].sum()
            ghg = area * removals_factor * c_to_co2
            return int(ghg)
        else:
            return 0

    return 0


def summarize_ghg(
    landuse_df: pd.DataFrame,
    forest_type_df: pd.DataFrame,
    years: int,
    emissions_factor: float = None,
    removals_factor: float = None,
    c_to_co2: float = 44 / 12,
    include_trees_outside_forest: bool = True,
) -> pd.DataFrame:
    """
    Summarize GHG emissions and removals.

    Args:
        landuse_df (pd.DataFrame): Land use DataFrame.
        forest_type_df (pd.DataFrame): Forest type DataFrame.
        years (int): Number of years between observations.
        emissions_factor (float, optional): Emission factor for trees outside forests.
        removals_factor (float, optional): Removal factor for trees outside forests.
        c_to_co2 (float, optional): Conversion factor from carbon to CO2.
        include_trees_outside_forest (bool, optional): Whether to include 'Trees Outside Forest' categories.

    Returns:
        pd.DataFrame: Summary DataFrame.
    """
    categories = [
        ("Forest Change", "To Cropland", "Emissions"),
        ("Forest Change", "To Grassland", "Emissions"),
        ("Forest Change", "To Settlement", "Emissions"),
        ("Forest Change", "To Wetland", "Emissions"),
        ("Forest Change", "To Other", "Emissions"),
        ("Forest Change", "Reforestation (Non-Forest to Forest)", "Removals"),
        ("Forest Remaining Forest", "Undisturbed", "Removals"),
        ("Forest Remaining Forest", "Fire", "Emissions"),
        ("Forest Remaining Forest", "Insect/Disease", "Emissions"),
        ("Forest Remaining Forest", "Harvest/Other", "Emissions"),
    ]

    if include_trees_outside_forest:
        categories.extend([
            ("Trees Outside Forest", "Tree canopy loss", "Emissions"),
            ("Trees Outside Forest", "Canopy maintained/gained", "Removals"),
        ])

    results = []
    for category, type_, emissions_removals in categories:
        area = calculate_area(category, type_, landuse_df, forest_type_df)
        ghg_flux = calculate_ghg_flux(
            category,
            type_,
            landuse_df,
            forest_type_df,
            years,
            emissions_factor=emissions_factor,
            removals_factor=removals_factor,
            c_to_co2=c_to_co2,
        )
        results.append(
            {
                "Category": category,
                "Type": type_,
                "Emissions/Removals": emissions_removals,
                "Area (ha, total)": area,
                "GHG Flux (t CO2e/year)": ghg_flux,
            }
        )

    summary_df = pd.DataFrame(results)

    return summary_df




def write_dataframes_to_csv(
    df_list: list, csv_file_path: str, space: int = 5
) -> None:
    """
    Write multiple DataFrames to a single CSV file with spacing.

    Args:
        df_list (list): List of DataFrames to write.
        csv_file_path (str): Output CSV file path.
        space (int, optional): Number of empty rows between DataFrames. Defaults to 5.
    """
    with open(csv_file_path, "w", newline="") as file:
        for i, df in enumerate(df_list):
            df.to_csv(file, index=False)
            if i < len(df_list) - 1:
                file.write("\n" * space)


# In funcs.py (or wherever 'save_results' is defined):

def save_results(
    landuse_result: pd.DataFrame,
    forest_type_result: pd.DataFrame,
    output_path: str,
    start_time: dt,
    geography_id: str = None
) -> None:
    """
    Save the land use and forest type results to CSV files, rounding to whole numbers.
    """

    # 1) Define the columns to round for each DataFrame
    #    (Adjust based on your actual columns that you want integer output)
    landuse_numeric_cols = [
        "Hectares", "CellCount",
        "carbon_ag_bg_us", "carbon_sd_dd_lt", "carbon_so",
        "fire_HA", "harvest_HA", "insect_damage_HA",
        "TreeCanopy_HA", "TreeCanopyLoss_HA",
        "Annual Emissions Forest to Non Forest CO2",
    ]
    forest_type_numeric_cols = [
        "Hectares", "fire_HA", "harvest_HA", "insect_damage_HA", "undisturbed_HA",
        "Annual_Removals_Undisturbed_CO2", "Annual_Removals_N_to_F_CO2",
        "Annual_Emissions_Fire_CO2", "Annual_Emissions_Harvest_CO2", "Annual_Emissions_Insect_CO2"
    ]

    # 2) Round columns in landuse_result
    for col in landuse_numeric_cols:
        if col in landuse_result.columns:
            landuse_result[col] = (
                pd.to_numeric(landuse_result[col], errors="coerce")
                  .round(0)
                  .astype("Int64")  # or just int, but "Int64" allows NaN if needed
            )

    # 3) Round columns in forest_type_result
    for col in forest_type_numeric_cols:
        if col in forest_type_result.columns:
            forest_type_result[col] = (
                pd.to_numeric(forest_type_result[col], errors="coerce")
                  .round(0)
                  .astype("Int64")
            )

    # 4) Write the CSVs
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    geography_suffix = f"_{geography_id}" if geography_id else ""

    landuse_csv = os.path.join(output_path, f"landuse_result{geography_suffix}_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result{geography_suffix}_{timestamp}.csv")

    landuse_result.to_csv(landuse_csv, index=False)
    forest_type_result.to_csv(forest_type_csv, index=False)

    # 5) Optionally, log processing time
    processing_time = dt.now() - start_time
    with open(os.path.join(output_path, f"processing_time{geography_suffix}.txt"), "w") as f:
        f.write(f"Total processing time: {processing_time}\n")

