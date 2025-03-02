"""
Helper functions used across multiple parts of the GP tool for land use change analysis.
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


def tabulate_area_by_stratification(
    stratification_raster: Raster,
    value_raster: str,
    output_name: str,
    pixel_size: int = 30,
    area_column_name: str = "Hectares",
) -> pd.DataFrame:
    """
    Tabulate area of different classes within a value raster for each stratification class.
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

    df_melted = pd.melt(
        cross_tab_df, id_vars=["OBJECTID", "VALUE"], value_name="Area"
    )

    df_melted["Raster_VALUE"] = df_melted["variable"].str.extract(r"VALUE_(\d+)").astype(int)
    df_melted["NLCD1_value"] = df_melted["VALUE"] // 100
    df_melted["NLCD2_value"] = df_melted["VALUE"] % 100

    df_melted["NLCD1_class"] = df_melted["NLCD1_value"].map(nlcdCategories)
    df_melted["NLCD2_class"] = df_melted["NLCD2_value"].map(nlcdCategories)

    df_filtered = df_melted[df_melted["Area"] > 0].copy()

    df_filtered[area_column_name] = df_filtered["Area"] / 10000  # convert m² -> hectares

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
    Calculate sum of values within a raster for each stratification class.
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
    strat_raster: "Raster",
    tree_canopy_source: str,
    aoi: str,
    cell_size: int,
    inventory_year1: int = None,
    inventory_year2: int = None,
    tcc_year1: int = None,
    tcc_year2: int = None,
) -> pd.DataFrame:
    """
    Calculate tree canopy averages and losses by stratification class,
    optionally scaling the loss if the TCC (tree canopy) period differs
    from the user's overall inventory period.

    Similar to the 'new' approach, but we drop "Hectares" and "CellCount"
    at the end (like the old approach) to avoid merging multiple "Hectares"
    columns downstream.
    """
    import arcpy
    from arcpy.sa import Con, Raster, ExtractByMask
    from funcs import zonal_sum_by_stratification
    import pandas as pd
    import numpy as np

    # If no tree canopy data provided, return empty
    if not tree_canopy_1 or not tree_canopy_2:
        arcpy.AddMessage("Skipping Tree Canopy - no data provided.")
        return pd.DataFrame(
            columns=["StratificationValue", "NLCD1_class", "NLCD2_class",
                     "TreeCanopy_HA", "TreeCanopyLoss_HA"]
        )

    # 1) Compute average canopy
    canopy_1_ras = Raster(tree_canopy_1)
    canopy_2_ras = Raster(tree_canopy_2)
    tree_canopy_avg = (canopy_1_ras + canopy_2_ras) / 2

    # 2) Compute canopy loss where canopy_2 < canopy_1
    tree_canopy_diff = Con(
        canopy_2_ras < canopy_1_ras,
        canopy_1_ras - canopy_2_ras,
        0
    )

    # 3) Mask both rasters to the AOI
    tree_canopy_avg_masked = ExtractByMask(tree_canopy_avg, aoi)
    tree_canopy_diff_masked = ExtractByMask(tree_canopy_diff, aoi)

    # 4) Zonal sums
    tc_avg_df = zonal_sum_by_stratification(
        strat_raster, tree_canopy_avg_masked, "TreeCanopy_HA", cell_size
    )
    tc_diff_df = zonal_sum_by_stratification(
        strat_raster, tree_canopy_diff_masked, "TreeCanopyLoss_HA", cell_size
    )

    # Merge average & difference
    tree_cover = tc_avg_df.merge(
        tc_diff_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer"
    )

    # 5) Convert pixel percentage => hectares, depending on source
    if "NLCD" in tree_canopy_source:
        # NLCD TCC is % in the pixel
        factor = 0.01 * (cell_size ** 2) / 10000.0
    elif "CBW" in tree_canopy_source:
        # CBW might be fractional or integer directly
        factor = (cell_size ** 2) / 10000.0
    else:
        # Default for "Local" or other
        factor = (cell_size ** 2) / 10000.0

    tree_cover["TreeCanopy_HA"] *= factor
    tree_cover["TreeCanopyLoss_HA"] *= factor

    # 6) If TCC period differs from inventory period, scale canopy loss
    if (
        inventory_year1 is not None
        and inventory_year2 is not None
        and tcc_year1 is not None
        and tcc_year2 is not None
    ):
        tcc_period_len = float(tcc_year2 - tcc_year1)
        inv_period_len = float(inventory_year2 - inventory_year1)
        if tcc_period_len > 0:
            scale_ratio = inv_period_len / tcc_period_len
            tree_cover["TreeCanopyLoss_HA"] *= scale_ratio
            arcpy.AddMessage(
                f"Scaled canopy loss by factor={scale_ratio:.2f}; "
                f"(inventory {inventory_year1}-{inventory_year2} vs TCC {tcc_year1}-{tcc_year2})."
            )
        else:
            arcpy.AddWarning(
                "Tree canopy period length is zero or invalid; skipping time-based scaling."
            )

    # 7) Drop "Hectares" and "CellCount" so we don't cause merges
    #    that lead to "Hectares_x" and "Hectares_y" later
    if "Hectares" in tree_cover.columns:
        tree_cover.drop(columns="Hectares", inplace=True, errors="ignore")
    if "CellCount" in tree_cover.columns:
        tree_cover.drop(columns="CellCount", inplace=True, errors="ignore")

    # Return canopy DataFrame with columns like:
    # ["StratificationValue", "NLCD1_class", "NLCD2_class",
    #  "TreeCanopy_HA", "TreeCanopyLoss_HA"]
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

    plantable_sum_df = zonal_sum_by_stratification(
        strat_raster, plantable_raster_masked, "Plantable_HA", cell_size
    )

    tree_cover_df = tree_cover_df.merge(
        plantable_sum_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer"
    )

    factor = (cell_size ** 2) / 10000
    tree_cover_df["Plantable_HA"] *= factor

    return tree_cover_df


def compute_disturbance_max(
    disturbance_rasters: list, strat_raster: Raster
) -> tuple:
    """
    Calculate the maximum disturbance class per pixel and tabulate area.
    """
    if len(disturbance_rasters) == 1:
        disturb_raster = disturbance_rasters[0]
    else:
        disturb_raster = CellStatistics(disturbance_rasters, "MAXIMUM", ignore_nodata="DATA")

    disturbance_df = tabulate_area_by_stratification(
        strat_raster, disturb_raster, output_name="Disturbance"
    )

    disturbance_df["DisturbanceClass"] = disturbance_df["Disturbance"].map(disturbanceLookup)

    disturbance_wide = disturbance_df.pivot_table(
        index=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        columns="DisturbanceClass",
        values="Hectares",
        aggfunc="sum"
    ).reset_index()

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
    Calculate sum of carbon stocks by stratification class.
    """
    carbon_ag_bg_us_df = zonal_sum_by_stratification(strat_raster, carbon_ag_bg_us, "carbon_ag_bg_us", cell_size)
    carbon_sd_dd_lt_df = zonal_sum_by_stratification(strat_raster, carbon_sd_dd_lt, "carbon_sd_dd_lt", cell_size)
    carbon_so_df = zonal_sum_by_stratification(strat_raster, carbon_so, "carbon_so", cell_size)

    carbon_df = carbon_ag_bg_us_df.merge(
        carbon_sd_dd_lt_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer"
    ).merge(
        carbon_so_df.drop(["Hectares", "CellCount"], axis=1),
        on=["StratificationValue", "NLCD1_class", "NLCD2_class"],
        how="outer"
    )

    cell_area_ha = (cell_size ** 2) / 10000
    carbon_df["carbon_ag_bg_us"] *= cell_area_ha
    carbon_df["carbon_sd_dd_lt"] *= cell_area_ha
    carbon_df["carbon_so"] *= cell_area_ha

    return carbon_df


def calculate_forest_to_nonforest_emissions(row: pd.Series, years_diff: int) -> float:
    """
    Calculate annual emissions (t CO2/yr) from forest to non-forest changes.
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
    Calculate disturbances (fire, insect damage, harvest) by forest age type.
    """
    forest_age_df["NLCD_1_ParentClass"] = forest_age_df["NLCD1_class"].map(nlcdParentRollupCategories)
    forest_age_df["NLCD_2_ParentClass"] = forest_age_df["NLCD2_class"].map(nlcdParentRollupCategories)

    disturbance_categories = set(disturbanceLookup.values())

    for disturbance in disturbance_categories:
        pixel_values = [k for k, v in disturbanceLookup.items() if v == disturbance]

        disturbance_condition = Con(
            InList(disturbance_raster, pixel_values), strat_raster, ""
        )

        temp_df = tabulate_area_by_stratification(
            disturbance_condition,
            forest_age_raster,
            output_name="ForestAgeTypeRegion",
            area_column_name=disturbance,
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


def merge_age_factors(
    forest_age_df: pd.DataFrame, forest_lookup_csv: str
) -> pd.DataFrame:
    """
    Merge forest age DataFrame with lookup table for emission/removal factors.
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

    merged_df = forest_age_df.merge(forest_table, on="ForestAgeTypeRegion", how="left")
    return merged_df


def calculate_forest_removals_and_emissions(
    forest_age_df: pd.DataFrame, year1: int, year2: int
) -> pd.DataFrame:
    """
    Calculate annual emissions and removals from forests.
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

    # Emissions from disturbances (fire, harvest, insect)
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
    """
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
    """
    Create a land cover transition matrix.
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


def calculate_area(
    category: str, type_: str, landuse_df: pd.DataFrame, forest_type_df: pd.DataFrame
) -> int:
    """
    Calculate total area for a specific category and type.
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
            subset = forest_type_df["Category"].isin(["Forest Remaining Forest", "Forest Remaining Forest (fire)"])
            ghg = forest_type_df.loc[subset, column].sum()
            return int(ghg)

    elif category == "Trees Outside Forest":
        if type_ == "Tree canopy loss" and "TreeCanopyLoss_HA" in landuse_df.columns:
            if emissions_factor is None:
                raise ValueError("Emissions factor is required for tree canopy loss emissions calculation.")
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopyLoss_HA"].sum()
            ghg = (area * emissions_factor * c_to_co2) / years  # annualize if desired
            return int(ghg)

        elif type_ == "Canopy maintained/gained" and "TreeCanopy_HA" in landuse_df.columns:
            if removals_factor is None:
                raise ValueError("Removals factor is required for canopy maintained/gained removals calculation.")
            area = landuse_df.loc[landuse_df["Category"] == "Nonforest to Nonforest", "TreeCanopy_HA"].sum()
            ghg = area * removals_factor * c_to_co2
            return int(ghg)

    return 0


import logging
logger = logging.getLogger("CommunityAnalysisLogger")

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
    """
    if landuse_df is None or forest_type_df is None:
        logger.error("summarize_ghg called with a None DataFrame.")
        return None
    if landuse_df.empty:
        logger.warning("landuse_df is empty. No GHG summary can be computed.")
    if forest_type_df.empty:
        logger.warning("forest_type_df is empty. No GHG summary can be computed.")

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

    try:
        for category, type_, emissions_removals in categories:
            area_val = calculate_area(category, type_, landuse_df, forest_type_df)
            flux_val = calculate_ghg_flux(
                category,
                type_,
                landuse_df,
                forest_type_df,
                years,
                emissions_factor=emissions_factor,
                removals_factor=removals_factor,
                c_to_co2=c_to_co2,
            )
            logger.debug(f"[summarize_ghg] cat={category}, type={type_}, area={area_val}, flux={flux_val}")
            results.append({
                "Category": category,
                "Type": type_,
                "Emissions/Removals": emissions_removals,
                "Area (ha, total)": area_val,
                "GHG Flux (t CO2e/year)": flux_val
            })

        summary_df = pd.DataFrame(results)
        logger.info(f"summarize_ghg produced {len(summary_df)} rows.")
        return summary_df

    except Exception as ex:
        logger.error("Error inside summarize_ghg", exc_info=True)
        return None


def write_dataframes_to_csv(
    df_list: list, csv_file_path: str, space: int = 5
) -> None:
    """
    Write multiple DataFrames to a single CSV file with spacing.
    """
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
    """
    Save the land use and forest type results to CSV files.
    """
    # Optionally round numeric columns to two decimals
    landuse_numeric_cols = ["TreeCanopy_HA", "TreeCanopyLoss_HA", ...]
    for col in landuse_numeric_cols:
        if col in landuse_result.columns:
            landuse_result[col] = landuse_result[col].round(2)

    forest_type_numeric_cols = ["Annual_Removals_Undisturbed_CO2", ...]
    for col in forest_type_numeric_cols:
        if col in forest_type_result.columns:
            forest_type_result[col] = forest_type_result[col].round(2)

    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    geography_suffix = f"_{geography_id}" if geography_id else ""

    landuse_csv = os.path.join(output_path, f"landuse_result{geography_suffix}_{timestamp}.csv")
    forest_type_csv = os.path.join(output_path, f"forest_type_result{geography_suffix}_{timestamp}.csv")

    landuse_result.to_csv(landuse_csv, index=False, float_format="%.2f")
    forest_type_result.to_csv(forest_type_csv, index=False, float_format="%.2f")

    processing_time = dt.now() - start_time
    with open(os.path.join(output_path, f"processing_time{geography_suffix}.txt"), "w") as f:
        f.write(f"Total processing time: {processing_time}\n")
