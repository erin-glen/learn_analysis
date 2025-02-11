import arcpy
import pandas as pd

from funcs import (
    tabulate_area_by_stratification,
    fill_na_values,
    merge_age_factors,
    determine_landuse_category,
    # Instead of calling determine_landuse_category() directly, we'll wrap it:
    calculate_forest_to_nonforest_emissions,
    calculate_forest_removals_and_emissions,
    calculate_disturbances,
    compute_disturbance_max,
    zonal_sum_carbon,
    create_landuse_stratification_raster,
    calculate_tree_canopy,
    calculate_plantable_areas,
)
from lookups import nlcdParentRollupCategories

###########################
# NEW SINGLE-PASS FUNCTION
###########################
def determine_landuse_category_with_fire(row: pd.Series, recategorize: bool) -> str:
    """
    If recategorize is True,  fire on a forest pixel is NOT labeled
    "Forest to Grassland"—it becomes "Forest Remaining Forest."
    Otherwise, fallback to the normal category logic.
    """
    base_cat = determine_landuse_category(row)
    if not recategorize:
        return base_cat

    # If recategorize==True, override ephemeral "Forest to Grassland + fire"
    if base_cat == "Forest to Grassland" and row.get("fire_HA", 0) > 0:
        return "Forest Remaining Forest"

    return base_cat

def perform_analysis(
        input_config: dict,
        cell_size: int,
        year1: int,
        year2: int,
        analysis_type: str = 'community',
        tree_canopy_source: str = None,
        recategorize_mode: bool = False
) -> tuple:

    try:
        from datetime import datetime as dt

        # 1) Unpack inputs
        aoi = input_config["aoi"]
        nlcd_1 = input_config["nlcd_1"]
        nlcd_2 = input_config["nlcd_2"]
        forest_age_raster = input_config["forest_age_raster"]
        carbon_ag_bg_us = input_config["carbon_ag_bg_us"]
        carbon_sd_dd_lt = input_config["carbon_sd_dd_lt"]
        carbon_so = input_config["carbon_so"]
        forest_lookup_csv = input_config["forest_lookup_csv"]
        disturbance_rasters = input_config["disturbance_rasters"]
        tree_canopy_1 = input_config.get("tree_canopy_1")
        tree_canopy_2 = input_config.get("tree_canopy_2")
        plantable_areas = input_config.get("plantable_areas")

        original_extent = arcpy.env.extent

        # Project AOI
        nlcd_sr = arcpy.Describe(nlcd_1).spatialReference
        arcpy.AddMessage("Projecting AOI to match NLCD raster spatial reference.")
        projected_aoi = arcpy.management.Project(aoi, "in_memory\\projected_aoi", nlcd_sr)
        aoi = projected_aoi

        arcpy.env.snapRaster = nlcd_1
        arcpy.env.cellSize = cell_size
        arcpy.env.overwriteOutput = True
        arcpy.env.extent = arcpy.Describe(aoi).extent

        # 2) Stratify
        arcpy.AddMessage("STEP 1: Creating land use stratification raster for all classes of land use")
        strat_raster = create_landuse_stratification_raster(nlcd_1, nlcd_2, aoi)

        # 3) If community, handle tree canopy
        tree_cover = None
        if analysis_type == 'community' and tree_canopy_1 and tree_canopy_2:
            arcpy.AddMessage("STEP 2: Summing up the tree canopy average & difference by strat class")
            tree_cover = calculate_tree_canopy(
                tree_canopy_1, tree_canopy_2, strat_raster, tree_canopy_source, aoi, cell_size
            )
            if plantable_areas and plantable_areas.lower() != "none":
                arcpy.AddMessage("STEP 2.5: Summing plantable areas by stratification class")
                tree_cover = calculate_plantable_areas(plantable_areas, strat_raster, tree_cover, aoi, cell_size)

        # 4) Disturbance + Carbon
        arcpy.AddMessage("STEP 3: Cross-tabulating disturbance area by stratification class")
        arcpy.AddMessage(f"Number of disturbance rasters: {len(disturbance_rasters)}")
        disturbance_wide, disturb_raster = compute_disturbance_max(disturbance_rasters, strat_raster)

        arcpy.AddMessage("STEP 4: Zonal statistics sum for carbon rasters by stratification class")
        carbon_df = zonal_sum_carbon(strat_raster, carbon_ag_bg_us, carbon_sd_dd_lt, carbon_so)

        # 5) Combine into landuse_df
        dfs_to_merge = [carbon_df, disturbance_wide]
        if tree_cover is not None:
            dfs_to_merge.append(tree_cover)

        landuse_df = dfs_to_merge[0]
        for df in dfs_to_merge[1:]:
            landuse_df = landuse_df.merge(df, how="outer", on=["StratificationValue", "NLCD1_class", "NLCD2_class"])

        # Instead of calling determine_landuse_category() directly,
        # we call our single-pass approach that checks recategorize_mode + fire_HA
        landuse_df["NLCD_1_ParentClass"] = landuse_df["NLCD1_class"].map(nlcdParentRollupCategories)
        landuse_df["NLCD_2_ParentClass"] = landuse_df["NLCD2_class"].map(nlcdParentRollupCategories)
        landuse_df["Category"] = landuse_df.apply(
            lambda row: determine_landuse_category_with_fire(row, recategorize_mode),
            axis=1
        )

        # 6) forest_age_df
        arcpy.AddMessage("STEP 5: Tabulating total area for forest age types by stratification class")
        forest_age_df = tabulate_area_by_stratification(strat_raster, forest_age_raster, "ForestAgeTypeRegion")

        arcpy.AddMessage("STEP 6: Disturbances by forest age")
        forest_age_df = calculate_disturbances(disturb_raster, strat_raster, forest_age_raster, forest_age_df)
        forest_age_df["NLCD_1_ParentClass"] = forest_age_df["NLCD1_class"].map(nlcdParentRollupCategories)
        forest_age_df["NLCD_2_ParentClass"] = forest_age_df["NLCD2_class"].map(nlcdParentRollupCategories)
        forest_age_df["Category"] = forest_age_df.apply(determine_landuse_category, axis=1)
        # NOTE: forest_age_df doesn't get ephemeral fire logic
        # (unless you want to unify that too, but typically this is used for disturbance-based emissions.)

        # (REMOVE the old recategorization block here)

        # 8) Annualize forest->nonforest
        years_diff = year2 - year1
        landuse_df["Annual Emissions Forest to Non Forest CO2"] = landuse_df.apply(
            lambda row: calculate_forest_to_nonforest_emissions(row, years_diff), axis=1
        )

        # 9) Fill NA, merge factors, disturbance emissions for forest_age_df
        arcpy.AddMessage("STEP 7: Calculating annual emissions from disturbances + annual removals")
        forest_age_df = fill_na_values(forest_age_df)
        forest_age_df = merge_age_factors(forest_age_df, forest_lookup_csv)
        forest_age_df = calculate_forest_removals_and_emissions(forest_age_df, year1, year2)

        return (
            landuse_df.sort_values(by=["Hectares"], ascending=False),
            forest_age_df.sort_values(by=["Hectares"], ascending=False),
        )

    except Exception as e:
        arcpy.AddError(f"An error occurred during analysis: {e}")
        return None, None
    finally:
        if original_extent:
            arcpy.env.extent = original_extent
        else:
            arcpy.env.extent = None
