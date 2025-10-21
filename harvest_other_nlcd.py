"""harvest_other_nlcd.py

Derives harvest/other disturbances using NLCD Tree Canopy Cover (TCC) rasters.
For each configured period, the script:
1. Loads the earliest and latest TCC rasters for the interval.
2. Computes canopy change (end minus start) and isolates canopy loss.
3. Classifies loss magnitude into four severity classes (0-4) based on
   configuration thresholds.
"""

import logging
import os

import arcpy

import disturbance_config as cfg


def _verify_year(year: int) -> str:
    """Return the raster path for *year* or raise a helpful error."""
    try:
        raster_path = cfg.NLCD_TCC_RASTERS[year]
    except KeyError as exc:
        raise KeyError(
            f"No NLCD Tree Canopy raster configured for year {year}. "
            "Update cfg.NLCD_TCC_RASTERS with the correct path."
        ) from exc

    if not arcpy.Exists(raster_path):
        raise FileNotFoundError(
            f"Configured NLCD Tree Canopy raster for {year} was not found: {raster_path}"
        )

    return raster_path


def _classify_loss(loss_raster):
    """Classify canopy loss magnitudes into four severity classes (0-4)."""
    breaks = cfg.NLCD_TCC_SEVERITY_BREAKS
    if len(breaks) != 4:
        raise ValueError(
            "cfg.NLCD_TCC_SEVERITY_BREAKS must contain exactly four upper-bound values "
            "for the severity classes."
        )

    b1, b2, b3, b4 = sorted(breaks)

    from arcpy.sa import Con  # Local import to honor Spatial Analyst licensing

    # 0 => no loss; classes 1-4 follow configured upper bounds.
    severity = Con(
        loss_raster <= 0,
        0,
        Con(
            loss_raster <= b1,
            1,
            Con(
                loss_raster <= b2,
                2,
                Con(
                    loss_raster <= b3,
                    3,
                    Con(loss_raster <= b4, 4, 4),
                ),
            ),
        ),
    )
    return severity


def main():
    logging.info("Starting NLCD Tree Canopy change-based harvest/other processing.")

    # ArcPy environment setup
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.snapRaster = cfg.NLCD_RASTER
    arcpy.env.extent = cfg.NLCD_RASTER
    arcpy.env.cellSize = cfg.NLCD_RASTER
    arcpy.env.outputCoordinateSystem = cfg.NLCD_RASTER

    from arcpy.sa import Abs, Con, Raster  # pylint: disable=import-error

    for period_name, years in cfg.TIME_PERIODS.items():
        if not years:
            logging.warning("Skipping '%s' because no years were provided.", period_name)
            continue

        start_year = min(years)
        end_year = max(years)
        if start_year == end_year:
            logging.warning(
                "Skipping '%s' because start and end year are the same (%s).",
                period_name,
                start_year,
            )
            continue

        out_change = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_change_{period_name}.tif")
        out_severity = os.path.join(cfg.NLCD_TCC_OUTPUT_DIR, f"nlcd_tcc_severity_{period_name}.tif")

        if arcpy.Exists(out_severity):
            logging.info(
                "Severity raster for '%s' already exists => %s. Skipping.",
                period_name,
                out_severity,
            )
            continue

        start_raster_path = _verify_year(start_year)
        end_raster_path = _verify_year(end_year)

        logging.info(
            "Processing period '%s' using start year %s and end year %s.",
            period_name,
            start_year,
            end_year,
        )
        logging.info("  Start raster => %s", start_raster_path)
        logging.info("  End raster   => %s", end_raster_path)

        start_raster = Raster(start_raster_path)
        end_raster = Raster(end_raster_path)

        change_raster = end_raster - start_raster
        if arcpy.Exists(out_change):
            logging.info(
                "Change raster for '%s' already exists => %s. Overwriting in memory only.",
                period_name,
                out_change,
            )
        else:
            change_raster.save(out_change)
            logging.info("Saved canopy change raster => %s", out_change)

        # Convert negative change (loss) to positive magnitude, otherwise 0
        loss_raster = Con(change_raster < 0, Abs(change_raster), 0)

        severity_raster = _classify_loss(loss_raster)
        severity_raster.save(out_severity)
        logging.info("Saved canopy loss severity raster => %s", out_severity)

    arcpy.CheckInExtension("Spatial")
    logging.info("NLCD Tree Canopy change processing completed successfully.")

if __name__ == "__main__":
    main()

