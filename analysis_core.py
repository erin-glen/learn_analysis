#analysis_core
import arcpy
import pandas as pd
from funcs import (
    tabulate_area_by_stratification,
    fill_na_values,
    merge_age_factors,
    determine_landuse_category,
    calculate_forest_to_nonforest_emissions,  # we'll pass years_diff now
    calculate_forest_removals_and_emissions,
    calculate_disturbances,
    compute_disturbance_max,
    zonal_sum_carbon,
    create_landuse_stratification_raster,
    calculate_tree_canopy,
    calculate_plantable_areas,
)
from lookups import nlcdParentRollupCategories


def perform_analysis(
        input_config: dict,
        cell_size: int,
        year1: int,
        year2: int,
        analysis_type: str = 'community',
        tree_canopy_source: str = None,
        recategorize_mode: bool = False
) -> tuple:
    """
    Performs the land use change analysis, returning two DataFrames:
      - landuse_df: Contains land-use transitions, disturbance, carbon, tree canopy
      - forest_age_df: Contains forest age class areas/disturbances and emissions/removals

    Both are now on an ANNUAL basis for fluxes (forest-to-nonforest and disturbances).
    """

    try:
        from datetime import datetime as dt

        # Unpack inputs
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

        # Save original environment settings
        original_extent = arcpy.env.extent

        # Get spatial reference of NLCD raster
        nlcd_sr = arcpy.Describe(nlcd_1).spatialReference

        # Project AOI to match NLCD raster spatial reference
        arcpy.AddMessage("Projecting AOI to match NLCD raster spatial reference.")
        projected_aoi = arcpy.management.Project(aoi, "in_memory\\projected_aoi", nlcd_sr)
        aoi = projected_aoi  # Update AOI to use the projected version

        # Set environment settings
        arcpy.env.snapRaster = nlcd_1
        arcpy.env.cellSize = cell_size
        arcpy.env.overwriteOutput = True
        arcpy.env.extent = arcpy.Describe(aoi).extent

        # Create stratification raster
        arcpy.AddMessage("STEP 1: Creating land use stratification raster for all classes of land use")
        strat_raster = create_landuse_stratification_raster(nlcd_1, nlcd_2, aoi)

        # Calculate tree canopy (community analysis only)
        if analysis_type == 'community' and tree_canopy_1 and tree_canopy_2:
            arcpy.AddMessage("STEP 2: Summing up the tree canopy average & difference by stratification class")
            tree_cover = calculate_tree_canopy(
                tree_canopy_1, tree_canopy_2, strat_raster, tree_canopy_source, aoi, cell_size
            )
            if plantable_areas and plantable_areas.lower() != "none":
                arcpy.AddMessage("STEP 2.5: Summing plantable areas by stratification class")
                tree_cover = calculate_plantable_areas(plantable_areas, strat_raster, tree_cover, aoi, cell_size)
        else:
            tree_cover = None

        # Compute disturbances
        arcpy.AddMessage("STEP 3: Cross-tabulating disturbance area by stratification class")
        arcpy.AddMessage(f"Number of disturbance rasters: {len(disturbance_rasters)}")
        disturbance_wide, disturb_raster = compute_disturbance_max(disturbance_rasters, strat_raster)

        # Compute carbon sums
        arcpy.AddMessage("STEP 4: Zonal statistics sum for carbon rasters by stratification class")
        carbon_df = zonal_sum_carbon(strat_raster, carbon_ag_bg_us, carbon_sd_dd_lt, carbon_so)

        # Merge dataframes
        dfs_to_merge = [carbon_df, disturbance_wide]
        if tree_cover is not None:
            dfs_to_merge.append(tree_cover)

        landuse_df = dfs_to_merge[0]
        for df in dfs_to_merge[1:]:
            landuse_df = landuse_df.merge(df, how="outer", on=["StratificationValue", "NLCD1_class", "NLCD2_class"])

        # Map NLCD classes to parent categories
        landuse_df["NLCD_1_ParentClass"] = landuse_df["NLCD1_class"].map(nlcdParentRollupCategories)
        landuse_df["NLCD_2_ParentClass"] = landuse_df["NLCD2_class"].map(nlcdParentRollupCategories)

        # Determine land-use change category
        landuse_df["Category"] = landuse_df.apply(determine_landuse_category, axis=1)

        # Optional recategorize step (disturbances)
        if recategorize_mode:
            recat_conditions = (
                (landuse_df["Category"] == "Forest to Grassland") &
                (
                    (landuse_df["fire_HA"] > 0) |
                    (landuse_df["insect_damage_HA"] > 0) |
                    (landuse_df["harvest_HA"] > 0)
                )
            )
            recategorize_count = recat_conditions.sum()
            if recategorize_count > 0:
                arcpy.AddMessage(
                    f"Recategorizing {recategorize_count} records from 'Forest to Grassland' -> 'Forest Remaining Forest'."
                )
                landuse_df.loc[recat_conditions, "Category"] = "Forest Remaining Forest"
            else:
                arcpy.AddMessage("No records met the recategorization criteria.")

        # Make forest-to-nonforest emissions ANNUAL instead of total
        years_diff = year2 - year1

        # Instead of "Total Emissions Forest to Non Forest CO2", we name it "Annual Emissions ..."
        landuse_df["Annual Emissions Forest to Non Forest CO2"] = landuse_df.apply(
            lambda row: calculate_forest_to_nonforest_emissions(row, years_diff),
            axis=1
        )

        # Step 5: Tabulate forest age areas
        arcpy.AddMessage("STEP 5: Tabulating total area for the forest age types by stratification class")
        forest_age_df = tabulate_area_by_stratification(
            strat_raster, forest_age_raster, output_name="ForestAgeTypeRegion"
        )

        # Step 6: Disturbances by forest age
        arcpy.AddMessage("STEP 6: Tabulating disturbance area for forest age types")
        forest_age_df = calculate_disturbances(disturb_raster, strat_raster, forest_age_raster, forest_age_df)

        # Step 7: Fill NA, merge factors, calculate annual flux
        arcpy.AddMessage("STEP 7: Calculating annual emissions from disturbances and annual removals")
        forest_age_df = fill_na_values(forest_age_df)
        forest_age_df = merge_age_factors(forest_age_df, forest_lookup_csv)
        forest_age_df = calculate_forest_removals_and_emissions(forest_age_df, year1, year2)
        # Note: 'calculate_forest_removals_and_emissions' already divides by years_diff for disturbances

        return (
            landuse_df.sort_values(by=["Hectares"], ascending=False),
            forest_age_df.sort_values(by=["Hectares"], ascending=False),
        )

    except Exception as e:
        arcpy.AddError(f"An error occurred during analysis: {e}")
        return None, None

    finally:
        # Restore environment settings
        if original_extent:
            arcpy.env.extent = original_extent
        else:
            arcpy.env.extent = None
