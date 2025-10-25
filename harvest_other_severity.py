#!/usr/bin/env python3
import os
import logging
import arcpy
from arcpy.sa import Abs, Con, Raster, SetNull, IsNull, Int  # noqa
import disturbance_config as cfg

def _exists(p): return bool(p) and (arcpy.Exists(p) or os.path.exists(p))

def _tcc(year):
    p = cfg.NLCD_TCC_RASTERS.get(year)
    return p if _exists(p) else None

def _set_env(ds):
    d = arcpy.Describe(ds)
    arcpy.env.snapRaster = ds
    arcpy.env.cellSize = ds
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference

def _save_tif(ras, out_tif, pixel_type, *, overwrite=False):
    if not overwrite and _exists(out_tif):
        logging.info("Skipping existing output: %s", out_tif)
        return False
    try:
        if arcpy.Exists(out_tif):
            arcpy.management.Delete(out_tif)
    except Exception:
        pass
    arcpy.management.CopyRaster(ras, out_tif, pixel_type=pixel_type, format="TIFF")
    if getattr(cfg, "COMPUTE_OUTPUT_STATS", False):
        try:
            arcpy.management.CalculateStatistics(out_tif)
        except Exception:
            pass
    return True

def _classify_loss(loss_r):
    b1, b2, b3, b4 = sorted(cfg.NLCD_TCC_SEVERITY_BREAKS)
    return Con(
        loss_r <= 0, 0,
        Con(
            loss_r <= b1, 1,
            Con(
                loss_r <= b2, 2,
                Con(
                    loss_r <= b3, 3,
                    Con(loss_r <= b4, 4, 4)
                ),
            ),
        ),
    )

def main():
    logging.info("Starting NLCD TCC pp-change processing.")
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.pyramid = "NONE"
    arcpy.env.parallelProcessingFactor = getattr(cfg, "PARALLEL_PROCESSING_FACTOR", "90%")
    if getattr(cfg, "SCRATCH_WORKSPACE", ""):
        arcpy.env.scratchWorkspace = cfg.SCRATCH_WORKSPACE
        arcpy.env.workspace = cfg.SCRATCH_WORKSPACE
    try:
        if not _exists(cfg.NLCD_RASTER):
            raise FileNotFoundError(cfg.NLCD_RASTER)
        _set_env(cfg.NLCD_RASTER)

        for pname, years in cfg.TIME_PERIODS.items():
            if not years:
                continue
            s, e = min(years), max(years)
            if s == e:
                continue
            sp, ep = _tcc(s), _tcc(e)
            if not (sp and ep):
                logging.error("Skipping %s (missing TCC).", pname)
                continue

            out_change   = os.path.join(cfg.NLCD_TCC_CHANGE_DIR, f"nlcd_tcc_change_{pname}.tif")
            out_severity = os.path.join(cfg.NLCD_HARVEST_SEVERITY_DIR, f"nlcd_tcc_severity_{pname}.tif")

            write_change = getattr(cfg, "WRITE_PP_CHANGE", True)
            expected = [out_severity]
            if write_change:
                expected.append(out_change)
            if all(_exists(p) for p in expected):
                logging.info("Outputs already exist for %s; skipping.", pname)
                continue

            start_r, end_r = Raster(sp), Raster(ep)
            inv_s = IsNull(start_r) | (start_r < 0) | (start_r > 100)
            inv_e = IsNull(end_r)   | (end_r   < 0) | (end_r   > 100)
            s_ok  = SetNull(inv_s, start_r)
            e_ok  = SetNull(inv_e, end_r)

            dpp = SetNull(IsNull(s_ok) | IsNull(e_ok), e_ok - s_ok)
            dpp = Con(dpp < -100, -100, Con(dpp > 100, 100, dpp))

            if write_change:
                if _save_tif(Int(dpp), out_change, "16_BIT_SIGNED"):
                    logging.info("Saved pp change => %s", out_change)
            else:
                logging.info("WRITE_PP_CHANGE=False; skipping write of %s", out_change)

            loss = Con(dpp < 0, Abs(dpp), 0)
            sev  = _classify_loss(loss)
            if _save_tif(sev, out_severity, "8_BIT_UNSIGNED"):
                logging.info("Saved pp-based severity => %s", out_severity)
        logging.info("Completed pp-change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")

if __name__ == "__main__":
    main()
