# analysis_core.py

import arcpy
import pandas as pd
from arcpy.sa import Raster, Con, InList, CellStatistics

from funcs import (
    tabulate_area_by_stratification,
    fill_na_values,
    merge_age_factors,
    determine_landuse_category,
    calculate_forest_to_nonforest_emissions,
    calculate_forest_removals_and_emissions,
    calculate_disturbances,
    compute_disturbance_max,  # We'll use a slightly modified approach with clipped rasters
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
    analysis_type: str = "community",
    tree_canopy_source: str = None,
    recategorize_mode: bool = False
) -> tuple:
    """
    Performs the land use change analysis in these steps:

      1) Project AOI to NLCD's spatial reference and set env to AOI extent.
      2) Create base stratification raster (clipped to AOI).
      3) Clip each disturbance raster to the AOI, then combine them => single 'disturb_raster'.
      4) If recategorize_mode=True, do pixel-level reclassification:
         - Where forest->grassland AND 'fire',
         - Flip second-year code back to first-year => 'forest->forest'.
      5) Calculate canopy (if needed), carbon, disturbances, forest age,
         using the corrected stratification.
      6) Return final (landuse_df, forest_age_df).
    """

    try:
        # Unpack input config
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

        # Save original environment extent
        original_extent = arcpy.env.extent

        # 1) Project AOI to match NLCD reference, set environment to AOI
        nlcd_sr = arcpy.Describe(nlcd_1).spatialReference
        arcpy.AddMessage("Projecting AOI to match NLCD raster spatial reference.")
        projected_aoi = arcpy.management.Project(aoi, "in_memory\\projected_aoi", nlcd_sr)
        aoi = projected_aoi

        arcpy.env.snapRaster = nlcd_1
        arcpy.env.cellSize = cell_size
        arcpy.env.overwriteOutput = True
        arcpy.env.extent = arcpy.Describe(aoi).extent  # We rely on AOI's bounding box

        # 2) Create the base stratification raster (both NLCD rasters clipped to AOI inside the function)
        arcpy.AddMessage("STEP 1: Creating base land use stratification raster (clipped to AOI).")
        strat_raster_base = create_landuse_stratification_raster(nlcd_1, nlcd_2, aoi)

        # 3) Clip each disturbance raster to AOI, then combine
        arcpy.AddMessage("STEP 2: Clipping disturbance rasters to AOI, then combining => single 'disturb_raster'.")

        clipped_dist_rasters = []
        for i, dist_path in enumerate(disturbance_rasters):
            out_clip = f"in_memory\\dist_clip_{i}"
            arcpy.AddMessage(f"  -> Clipping {dist_path} to AOI => {out_clip}")
            arcpy.management.Clip(
                in_raster=dist_path,
                rectangle="#",  # We'll rely on env.extent
                out_raster=out_clip,
                in_template_dataset=aoi,
                nodata_value="",
                clipping_geometry="ClippingGeometry",
                maintain_clipping_extent=True
            )
            clipped_dist_rasters.append(out_clip)

        if len(clipped_dist_rasters) == 0:
            # No disturbance rasters? Then we can't proceed with recat
            arcpy.AddMessage("No disturbance rasters for this period.")
            disturb_raster = None
        elif len(clipped_dist_rasters) == 1:
            disturb_raster = Raster(clipped_dist_rasters[0])
        else:
            disturb_raster = CellStatistics(clipped_dist_rasters, "MAXIMUM", ignore_nodata="DATA")

        # 4) Pixel-level reclassification if recategorize_mode
        #    forest->grassland + fire => forest->forest
        if recategorize_mode and disturb_raster is not None:
            arcpy.AddMessage("Recategorize mode ON: forest->grassland + fire => forest->forest (pixel-level).")

            # Identify forest vs. grass
            forest_codes = [41, 42, 43, 90]  # Deciduous/Evergreen/Mixed/Woody
            grass_codes = [52, 71, 81]       # Shrub/Scrub, Herbaceous, Hay/Pasture

            # Break out the year1/year2 codes
            nlcd1 = strat_raster_base // 100
            nlcd2 = strat_raster_base % 100

            is_forest_to_grass = InList(nlcd1, forest_codes) & InList(nlcd2, grass_codes)
            is_fire = (disturb_raster == 10)

            fix_pixels = is_forest_to_grass & is_fire

            corrected_strat_raster = Con(
                fix_pixels,
                (nlcd1 * 100) + nlcd1,  # second-year => first-year
                strat_raster_base
            )
        else:
            arcpy.AddMessage("Recategorize mode OFF or no disturbance. Using original stratification.")
            corrected_strat_raster = strat_raster_base

        # 5) Calculate tree canopy & plantable areas (for community analysis), with corrected stratification
        arcpy.AddMessage("STEP 3: Tree canopy & plantable areas (if applicable).")
        if analysis_type == "community" and tree_canopy_1 and tree_canopy_2:
            tree_cover = calculate_tree_canopy(
                tree_canopy_1,
                tree_canopy_2,
                corrected_strat_raster,
                tree_canopy_source,
                aoi,
                cell_size
            )
            if plantable_areas and plantable_areas.lower() != "none":
                tree_cover = calculate_plantable_areas(
                    plantable_areas,
                    corrected_strat_raster,
                    tree_cover,
                    aoi,
                    cell_size
                )
        else:
            tree_cover = None

        # Disturbance area with the final stratification
        arcpy.AddMessage("STEP 4: Cross-tabulating disturbance area with corrected stratification.")
        # We'll pass a list with a single item if disturb_raster exists:
        if disturb_raster is not None:
            disturbance_wide, final_disturb_raster = compute_disturbance_max([disturb_raster], corrected_strat_raster)
        else:
            # If no disturbance rasters, return an empty frame
            import pandas as pd
            disturbance_wide = pd.DataFrame(columns=[
                "StratificationValue", "NLCD1_class", "NLCD2_class", "fire_HA", "harvest_HA", "insect_damage_HA"
            ])
            final_disturb_raster = None

        # 6) Carbon sums
        arcpy.AddMessage("STEP 5: Zonal statistics for carbon.")
        carbon_df = zonal_sum_carbon(
            corrected_strat_raster,
            carbon_ag_bg_us,
            carbon_sd_dd_lt,
            carbon_so
        )

        # Merge data => landuse_df
        arcpy.AddMessage("STEP 6: Merging carbon, disturbance, and tree canopy info.")
        landuse_df = carbon_df.merge(
            disturbance_wide, how="outer",
            on=["StratificationValue", "NLCD1_class", "NLCD2_class"]
        )
        if tree_cover is not None and not tree_cover.empty:
            landuse_df = landuse_df.merge(
                tree_cover, how="outer",
                on=["StratificationValue", "NLCD1_class", "NLCD2_class"]
            )

        # Add parent classes and categories
        landuse_df["NLCD_1_ParentClass"] = landuse_df["NLCD1_class"].map(nlcdParentRollupCategories)
        landuse_df["NLCD_2_ParentClass"] = landuse_df["NLCD2_class"].map(nlcdParentRollupCategories)
        landuse_df["Category"] = landuse_df.apply(determine_landuse_category, axis=1)

        # Forest->nonforest emissions => annual
        years_diff = year2 - year1
        landuse_df["Annual Emissions Forest to Non Forest CO2"] = landuse_df.apply(
            lambda row: calculate_forest_to_nonforest_emissions(row, years_diff),
            axis=1
        )

        # 7) Forest age tabulation using the corrected stratification
        arcpy.AddMessage("STEP 7: Tabulating forest age (corrected stratification).")
        forest_age_df = tabulate_area_by_stratification(
            corrected_strat_raster,
            forest_age_raster,
            output_name="ForestAgeTypeRegion"
        )

        # Disturbances by forest age
        arcpy.AddMessage("STEP 8: Disturbances by forest age classes.")
        if final_disturb_raster is not None:
            forest_age_df = calculate_disturbances(
                final_disturb_raster,
                corrected_strat_raster,
                forest_age_raster,
                forest_age_df
            )

        # Fill NA, merge factors, final forest emissions
        arcpy.AddMessage("STEP 9: Fill NA, merge age factors, compute forest emissions.")
        forest_age_df = fill_na_values(forest_age_df)
        forest_age_df = merge_age_factors(forest_age_df, forest_lookup_csv)
        forest_age_df = calculate_forest_removals_and_emissions(forest_age_df, year1, year2)

        # Return final DataFrames
        return (
            landuse_df.sort_values(by=["Hectares"], ascending=False),
            forest_age_df.sort_values(by=["Hectares"], ascending=False),
        )

    except Exception as e:
        arcpy.AddError(f"An error occurred during analysis: {e}")
        return None, None

    finally:
        # Restore original extent
        if original_extent:
            arcpy.env.extent = original_extent
        else:
            arcpy.env.extent = None
