"""
Helper functions used across multiple parts of the GP tool for land use change analysis.

This module includes functions for data conversion, raster processing,
data summarization, and calculation of emissions and removals.
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
    """
    arcpy.management.Clip(nlcd_raster1, "#", "in_memory/nlcd_before", aoi, "", "ClippingGeometry")
    arcpy.management.Clip(nlcd_raster2, "#", "in_memory/nlcd_after", aoi, "", "ClippingGeometry")

    strat_raster = Raster("in_memory/nlcd_before") * 100 + Raster("in_memory/nlcd_after")
    return strat_raster


def rollup_to_parent_class(
    df: pd.DataFrame, columns_to_aggregate: list, group_by: list = None
) -> pd.DataFrame:
    """
    Roll up values to parent NLCD categories using sum aggregation.
    """
    df_copy = df.copy()
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
    return pd.pivot_table(
        df, values=values, index=[index_category], columns=[column_category], aggfunc=np.sum
    )


def tabulate_area_by_stratification(
    stratification_raster: Raster,
    value_raster: str,
    output_name: str,
    pixel_size: int = 30,
    area_column_name: str = "Hectares",
) -> pd.DataFrame:
    """
    Tabulate the area of different classes within a value raster for each stratification class.
    """
    cross_tab = TabulateArea(
        stratification_raster,
        "Value",
        value_raster,
        "Value",
        "in_memory/cross_tab",
        pixel_size,
    )

    cross_tab_df = feature_class_to_pandas_dataframe(cross_tab, "*")
    df_melted = pd.melt(cross_tab_df, id_vars=["OBJECTID", "VALUE"], value_name="Area")
    df_melted["Raster_VALUE"] = df_melted["variable"].str.extract(r"VALUE_(\d+)").astype(int)
    df_melted["NLCD1_value"] = df_melted["VALUE"] // 100
    df_melted["NLCD2_value"] = df_melted["VALUE"] % 100
    df_melted["NLCD1_class"] = df_melted["NLCD1_value"].map(nlcdCategories)
    df_melted["NLCD2_class"] = df_melted["NLCD2_value"].map(nlcdCategories)
    df_filtered = df_melted[df_melted["Area"] > 0].copy()
    df_filtered[area_column_name] = df_filtered["Area"] / 10000

    df_filtered = df_filtered.rename(
        columns={"Raster_VALUE": output_name, "VALUE": "StratificationValue"}
    )

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
    """
    zonal_stats = ZonalStatisticsAsTable(
        stratification_raster,
        "Value",
        value_raster,
        "in_memory/zonal_stats",
        statistics_type="SUM",
    )
    zs_df = feature_class_to_pandas_dataframe(zonal_stats, ["VALUE", "COUNT", "SUM"])
    zs_df.columns = ["StratificationValue", "CellCount", column_name]

    zs_df["NLCD1_value"] = zs_df["StratificationValue"] // 100
    zs_df["NLCD2_value"] = zs_df["StratificationValue"] % 100
    zs_df["NLCD1_class"] = zs_df["NLCD1_value"].map(nlcdCategories)
    zs_df["NLCD2_class"] = zs_df["NLCD2_value"].map(nlcdCategories)
    zs_df["Hectares"] = (zs_df["CellCount"] * cell_size ** 2) / 10000

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
    """
    if not tree_canopy_1 or not tree_canopy_2:
        arcpy.AddMessage("Skipping Tree Canopy - no data provided.")
        return pd.DataFrame(
            columns=["StratificationValue", "NLCD1_class", "NLCD2_class", "TreeCanopy_HA", "TreeCanopyLoss_HA"]
        )

    tree_canopy_avg = (Raster(tree_canopy_1) + Raster(tree_canopy_2)) / 2
    tree_canopy_diff = Con(
        Raster(tree_canopy_2) < Raster(tree_canopy_1),
        Raster(tree_canopy_1) - Raster(tree_canopy_2),
        0,
    )

    arcpy.AddMessage("Applying AOI mask using ExtractByMask.")
    tree_canopy_avg_masked = ExtractByMask(tree_canopy_avg, aoi)
    tree_canopy_diff_masked = ExtractByMask(tree_canopy_diff, aoi)

    tc_avg_df = zonal_sum_by_stratification(strat_raster, tree_canopy_avg_masked, "TreeCanopy_HA", cell_size)
    tc_diff_df = zonal_sum_by_stratification(strat_raster, tree_canopy_diff_masked, "TreeCanopyLoss_HA", cell_size)

    tree_cover = tc_avg_df.merge(
        tc_diff_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )

    if "NLCD" in tree_canopy_source:
        factor = 0.01 * (cell_size ** 2) / 10000
    elif "CBW" in tree_canopy_source:
        factor = (cell_size ** 2) / 10000
    else:
        factor = (cell_size ** 2) / 10000

    tree_cover["TreeCanopy_HA"] *= factor
    tree_cover["TreeCanopyLoss_HA"] *= factor
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
    """
    if plantable_areas_raster.lower() == "none":
        arcpy.AddMessage("Skipping Plantable Areas - no data provided.")
        return tree_cover_df

    arcpy.AddMessage("Applying AOI mask to plantable areas raster.")
    plantable_raster_masked = ExtractByMask(plantable_areas_raster, aoi)
    plantable_sum_df = zonal_sum_by_stratification(strat_raster, plantable_raster_masked, "Plantable_HA", cell_size)
    tree_cover_df = tree_cover_df.merge(
        plantable_sum_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )
    factor = (cell_size ** 2) / 10000
    tree_cover_df["Plantable_HA"] *= factor
    return tree_cover_df


def compute_disturbance_max(disturbance_rasters: list, strat_raster: Raster) -> tuple:
    """
    Calculate the maximum disturbance class per pixel and tabulate area.
    """
    if len(disturbance_rasters) == 1:
        disturb_raster = disturbance_rasters[0]
    else:
        disturb_raster = CellStatistics(disturbance_rasters, "MAXIMUM", ignore_nodata="DATA")

    disturbance_df = tabulate_area_by_stratification(strat_raster, disturb_raster, output_name="Disturbance")
    disturbance_df["DisturbanceClass"] = disturbance_df["Disturbance"].map(disturbanceLookup)
    disturbance_wide = disturbance_df.pivot_table(
        index=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        columns="DisturbanceClass",
        values="Hectares",
        aggfunc="sum",
    ).reset_index()

    disturbance_wide.rename(
        columns={
            "fire": "fire_HA",
            "harvest": "harvest_HA",
            "insect_damage": "insect_damage_HA"
        },
        inplace=True
    )
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
    """
    carbon_ag_bg_us_df = zonal_sum_by_stratification(strat_raster, carbon_ag_bg_us, "carbon_ag_bg_us", cell_size)
    carbon_sd_dd_lt_df = zonal_sum_by_stratification(strat_raster, carbon_sd_dd_lt, "carbon_sd_dd_lt", cell_size)
    carbon_so_df = zonal_sum_by_stratification(strat_raster, carbon_so, "carbon_so", cell_size)

    carbon_df = carbon_ag_bg_us_df.merge(
        carbon_sd_dd_lt_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    ).merge(
        carbon_so_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer",
    )

    cell_area_ha = (cell_size ** 2) / 10000
    carbon_df["carbon_ag_bg_us"] *= cell_area_ha
    carbon_df["carbon_sd_dd_lt"] *= cell_area_ha
    carbon_df["carbon_so"] *= cell_area_ha
    return carbon_df


def calculate_forest_to_nonforest_emissions(row: pd.Series, years_diff: int) -> float:
    """
    Calculate *annual* emissions (t CO2/yr) from forest to non-forest changes,
    dividing total flux by 'years_diff'.
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
        total_c_loss = ag_bg + sd_dd + so
        total_co2_loss = total_c_loss * (44 / 12)
        return total_co2_loss / years_diff
    else:
        return 0.0


def determine_landuse_category(row: pd.Series) -> str:
    """
    Determine the land use change category based on NLCD parent classes.
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
    Calculate disturbances (fire, insect, harvest) by forest age type.
    Ensures output columns are named 'fire_HA', 'harvest_HA', 'insect_damage_HA'.
    """

    forest_age_df["NLCD_1_ParentClass"] = forest_age_df["NLCD1_class"].map(nlcdParentRollupCategories)
    forest_age_df["NLCD_2_ParentClass"] = forest_age_df["NLCD2_class"].map(nlcdParentRollupCategories)

    disturbance_categories = set(disturbanceLookup.values())  # e.g. {'fire_HA','harvest_HA','insect_damage_HA'}

    for disturbance in disturbance_categories:
        pixel_values = [k for k, v in disturbanceLookup.items() if v == disturbance]

        disturbance_condition = Con(
            InList(disturbance_raster, pixel_values),
            strat_raster,
            ""
        )
        out_col_name = f"{disturbance}"  # e.g. "fire_HA"
        temp_df = tabulate_area_by_stratification(
            disturbance_condition,
            forest_age_raster,
            output_name="ForestAgeTypeRegion",
            area_column_name=out_col_name,
        )
        forest_age_df = forest_age_df.merge(
            temp_df,
            on=["StratificationValue", "NLCD1_class", "NLCD2_class", "ForestAgeTypeRegion"],
            how="outer",
        )

    return forest_age_df


def fill_na_values(forest_age_df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill NA values with zeros and calculate undisturbed forest area.
    """
    forest_age_df["fire_HA"] = forest_age_df["fire_HA"].fillna(0)
    forest_age_df["insect_damage_HA"] = forest_age_df["insect_damage_HA"].fillna(0)
    forest_age_df["harvest_HA"] = forest_age_df["harvest_HA"].fillna(0)

    forest_age_df["undisturbed_HA"] = (
        forest_age_df["Hectares"]
        - forest_age_df["fire_HA"]
        - forest_age_df["harvest_HA"]
        - forest_age_df["insect_damage_HA"]
    )
    forest_age_df["Category"] = forest_age_df.apply(determine_landuse_category, axis=1)
    return forest_age_df


def merge_age_factors(forest_age_df: pd.DataFrame, forest_lookup_csv: str) -> pd.DataFrame:
    columns_to_use = [
        "ForestAgeTypeRegion",
        "Nonforest to Forest Removal Factor",
        "Forests Remaining Forest Removal Factor",
        "Fire Emissions Factor",
        "Insect Emissions Factor",
        "Harvest Emissions Factor",
    ]
    forest_table = pd.read_csv(forest_lookup_csv, usecols=columns_to_use)
    merged_df = forest_age_df.merge(forest_table, on="ForestAgeTypeRegion", how="left")
    return merged_df


def calculate_forest_removals_and_emissions(
    forest_age_df: pd.DataFrame, year1: int, year2: int
) -> pd.DataFrame:
    """
    Calculate annual emissions and removals from forests.
    """
    years_difference = year2 - year1
    forest_age_df["Annual_Removals_Undisturbed_CO2"] = (
        forest_age_df["undisturbed_HA"] * forest_age_df["Forests Remaining Forest Removal Factor"] * (44 / 12)
    )
    forest_age_df["Annual_Removals_N_to_F_CO2"] = (
        forest_age_df["Hectares"] * forest_age_df["Nonforest to Forest Removal Factor"] * (44 / 12)
    )
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
    nonforest_df = landuse_df[landuse_df["Category"] == "Nonforest to Nonforest"]
    sum_columns = ["Hectares", "TreeCanopy_HA", "TreeCanopyLoss_HA"]
    if "Plantable_HA" in landuse_df.columns:
        sum_columns.append("Plantable_HA")

    summary = nonforest_df.groupby("NLCD_2_ParentClass")[sum_columns].sum().reset_index()
    summary["Percent Tree Cover"] = (summary["TreeCanopy_HA"] / summary["Hectares"]) * 100
    if "Plantable_HA" in summary.columns:
        summary["Percent Plantable"] = (summary["Plantable_HA"] / summary["Hectares"]) * 100

    summary.drop("Hectares", axis=1, inplace=True)
    numerical_cols = summary.select_dtypes(include=["float", "int"]).columns
    summary[numerical_cols] = summary[numerical_cols].round().astype(int)
    sort_order = ["Grassland", "Cropland", "Settlement", "Wetland", "Other Land"]
    summary["NLCD_2_ParentClass"] = pd.Categorical(summary["NLCD_2_ParentClass"], categories=sort_order, ordered=True)
    summary.sort_values("NLCD_2_ParentClass", inplace=True)
    summary.rename(columns={"NLCD_2_ParentClass": "Category"}, inplace=True)
    return summary


def create_land_cover_transition_matrix(landuse_df: pd.DataFrame) -> pd.DataFrame:
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
    transition_matrix = landuse_df.pivot_table(
        index="NLCD1_class",
        columns="NLCD2_class",
        values="Hectares",
        aggfunc="sum",
        fill_value=0,
    )
    transition_matrix = transition_matrix.reindex(index=class_order, columns=class_order, fill_value=0)
    transition_matrix["Total"] = transition_matrix.sum(axis=1)
    transition_matrix.loc["Total"] = transition_matrix.sum()
    transition_matrix.reset_index(inplace=True)
    numerical_cols = transition_matrix.select_dtypes(include=["float", "int"]).columns
    for col in numerical_cols:
        if col != "NLCD1_class":
            transition_matrix[col] = transition_matrix[col].astype(int)
    return transition_matrix


def calculate_area(category: str, type_: str, landuse_df: pd.DataFrame, forest_type_df: pd.DataFrame) -> int:
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
            area = forest_type_df.loc[forest_type_df["Category"] == "Forest Remaining Forest", column].sum()
            return int(area)

    elif category == "Trees Outside Forest":
        if type_ == "Tree canopy loss" and "TreeCanopyLoss_HA" in landuse_df.columns:
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopyLoss_HA"].sum()
            return int(area)
        elif type_ == "Canopy maintained/gained" and "TreeCanopy_HA" in landuse_df.columns:
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopy_HA"].sum()
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
                ghg = forest_type_df.loc[forest_type_df["Category"] == key, "Annual_Removals_N_to_F_CO2"].sum()
            else:
                ghg = landuse_df.loc[landuse_df["Category"] == key, "Annual Emissions Forest to Non Forest CO2"].sum()
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
            ghg = forest_type_df.loc[forest_type_df["Category"] == "Forest Remaining Forest", column].sum()
            return int(ghg)

    elif category == "Trees Outside Forest":
        if type_ == "Tree canopy loss" and "TreeCanopyLoss_HA" in landuse_df.columns:
            if emissions_factor is None:
                raise ValueError("Emissions factor is required for tree canopy loss emissions calculation.")
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopyLoss_HA"].sum()
            ghg = (area * emissions_factor * c_to_co2) / years
            return int(ghg)
        elif type_ == "Canopy maintained/gained" and "TreeCanopy_HA" in landuse_df.columns:
            if removals_factor is None:
                raise ValueError("Removals factor is required for canopy maintained/gained removals calculation.")
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopy_HA"].sum()
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


def write_dataframes_to_csv(df_list: list, csv_file_path: str, space: int = 5) -> None:
    with open(csv_file_path, "w", newline="") as file:
        for i, df in enumerate(df_list):
            df.to_csv(file, index=False)
            if i < len(df_list) - 1:
                file.write("\n" * space)


def save_results(
    landuse_result: pd.DataFrame,
    forest_type_result: pd.DataFrame,
    output_path: str,
    start_time: dt,
    geography_id: str = None
) -> None:
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

    for col in landuse_numeric_cols:
        if col in landuse_result.columns:
            landuse_result[col] = pd.to_numeric(landuse_result[col], errors="coerce").round(0).astype("Int64")

    for col in forest_type_numeric_cols:
        if col in forest_type_result.columns:
            forest_type_result[col] = pd.to_numeric(forest_type_result[col], errors="coerce").round(0).astype("Int64")

    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    geography_suffix = f"_{geography_id}" if geography_id else ""
    landuse_csv = os.path.join(output_path, f"landuse_result{geography_suffix}_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result{geography_suffix}_{timestamp}.csv")

    landuse_result.to_csv(landuse_csv, index=False)
    forest_type_result.to_csv(forest_type_csv, index=False)

    processing_time = dt.now() - start_time
    with open(os.path.join(output_path, f"processing_time{geography_suffix}.txt"), "w") as f:
        f.write(f"Total processing time: {processing_time}\n")
