#!/usr/bin/env python3
import os
import logging
import arcpy
from arcpy.sa import Abs, Con, Float, Raster, SetNull, IsNull, Int  # noqa
import disturbance_config as cfg

# Toggle: store percent-change as INT16 with scale (e.g., 0.1% units) to shrink files
SAVE_PCT_AS_INT16 = False
PCT_SCALE = 10  # -100..100% -> -1000..1000 (0.1% units)

def _exists(p): return bool(p) and (arcpy.Exists(p) or os.path.exists(p))

def _tcc(y):
    p = cfg.NLCD_TCC_RASTERS.get(y)
    return p if _exists(p) else None

def _set_env(ds):
    d = arcpy.Describe(ds)
    arcpy.env.snapRaster = ds
    arcpy.env.cellSize = ds
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference

def _save_tif(ras, out_tif, pixel_type):
    try:
        if arcpy.Exists(out_tif):
            arcpy.management.Delete(out_tif)
    except Exception:
        pass
    with arcpy.EnvManager(compression="LZW", pyramid="NONE"):
        arcpy.management.CopyRaster(ras, out_tif, pixel_type=pixel_type, format="TIFF")
    if getattr(cfg, "COMPUTE_OUTPUT_STATS", False):
        try:
            arcpy.management.CalculateStatistics(out_tif)
        except Exception:
            pass

def _pct_breaks():
    return getattr(cfg, "NLCD_TCC_PCT_SEVERITY_BREAKS", cfg.NLCD_TCC_SEVERITY_BREAKS)

def _classify_loss(loss_r):
    b1, b2, b3, b4 = sorted(_pct_breaks())
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
    logging.info("Starting NLCD TCC percent-change processing.")
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

        min_base = getattr(cfg, "NLCD_TCC_PCT_MIN_BASE", 0)

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

            start_r, end_r = Raster(sp), Raster(ep)

            inv_s = IsNull(start_r) | (start_r < 0) | (start_r > 100)
            inv_e = IsNull(end_r)   | (end_r   < 0) | (end_r   > 100)
            s_small = start_r <= min_base

            s_ok = SetNull(inv_s | s_small, start_r)
            e_ok = SetNull(inv_e,          end_r)

            pct = SetNull(IsNull(s_ok) | IsNull(e_ok), Float((e_ok - s_ok) / s_ok * 100))
            pct = Con(pct < -100, -100, Con(pct > 100, 100, pct))

            out_change   = os.path.join(cfg.NLCD_TCC_PCT_CHANGE_DIR, f"nlcd_tcc_pct_change_{pname}.tif")
            out_severity = os.path.join(cfg.NLCD_HARVEST_PCT_SEV_DIR,  f"nlcd_tcc_pct_severity_{pname}.tif")

            if getattr(cfg, "WRITE_PCT_CHANGE", True):
                if SAVE_PCT_AS_INT16:
                    _save_tif(Int(pct * PCT_SCALE), out_change, "16_BIT_SIGNED")
                    logging.info("Saved percent change INT16 (scale=%s) => %s", PCT_SCALE, out_change)
                else:
                    _save_tif(pct, out_change, "32_BIT_FLOAT")
                    logging.info("Saved percent change FLOAT32 => %s", out_change)
            else:
                logging.info("WRITE_PCT_CHANGE=False; skipping write of %s", out_change)

            loss = Con(pct < 0, Abs(pct), 0)
            sev  = _classify_loss(loss)
            _save_tif(sev, out_severity, "8_BIT_UNSIGNED")
            logging.info("Saved percent-based severity => %s", out_severity)

        logging.info("Completed percent-change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")

if __name__ == "__main__":
    main()
