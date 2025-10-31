#!/usr/bin/env python3
"""
NLCD TCC — ABSOLUTE change and severity (percentage points)
-----------------------------------------------------------
For each period {start,end}:
  1) Validate inputs to 0–100 and mask NoData.
  2) Compute absolute pp change: dpp = end - start (clip to [-100, 100]).
     FAST PATH: If the change TIFF already exists, reuse it instead of recomputing.
  3) Derive loss magnitude only: loss = |dpp| where dpp < 0 else 0.
  4) Classify loss to severity 0–4 using cfg.NLCD_TCC_SEVERITY_BREAKS (via Reclassify).

Outputs:
  - {NLCD_TCC_CHANGE_DIR}/nlcd_tcc_change_{period}.tif           [optional; Int16]
  - {NLCD_HARVEST_SEVERITY_DIR}/nlcd_tcc_severity_{period}.tif   [Byte]
"""

import os
import time
import logging
import arcpy
from arcpy.sa import Abs, Con, Raster, SetNull, IsNull, Int, Reclassify, RemapRange  # noqa
import disturbance_config as cfg

# Optional fast-path toggle (defaults to True if not present in cfg)
USE_EXISTING_PP_CHANGE_FOR_SEVERITY = getattr(cfg, "USE_EXISTING_PP_CHANGE_FOR_SEVERITY", True)

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

def _save_tif(ras, out_tif, pixel_type, *, overwrite=False, lzw=True):
    # Skip if present (unless overwrite=True)
    if not overwrite and _exists(out_tif):
        logging.info("Skipping existing output: %s", out_tif)
        return False
    try:
        if arcpy.Exists(out_tif):
            arcpy.management.Delete(out_tif)
    except Exception:
        pass
    # LZW typically gives faster end-to-end for these rasters (tiny value range)
    if lzw:
        with arcpy.EnvManager(compression="LZW", pyramid="NONE"):
            arcpy.management.CopyRaster(ras, out_tif, pixel_type=pixel_type, format="TIFF")
    else:
        with arcpy.EnvManager(pyramid="NONE"):
            arcpy.management.CopyRaster(ras, out_tif, pixel_type=pixel_type, format="TIFF")
    if getattr(cfg, "COMPUTE_OUTPUT_STATS", False):
        try:
            arcpy.management.CalculateStatistics(out_tif)
        except Exception:
            pass
    return True

def _classify_loss_fast(loss_int):
    """
    Reclassify integer loss magnitude to severity 0..4 using RemapRange.
    Assumes loss_int is Int in [0..100] with NoData outside valid areas.
    """
    b1, b2, b3, b4 = sorted(cfg.NLCD_TCC_SEVERITY_BREAKS)
    # Map: 0->0; 1..b1->1; (b1+1)..b2->2; (b2+1)..b3->3; (b3+1)..b4->4
    remap = RemapRange([
        [0, 0, 0],
        [1, b1, 1],
        [b1 + 1, b2, 2],
        [b2 + 1, b3, 3],
        [b3 + 1, b4, 4],
    ])
    return Reclassify(loss_int, "Value", remap, "NODATA")

def _log_grid_info(tag, ras_path):
    try:
        d = arcpy.Describe(ras_path)
        cs = getattr(d, "spatialReference", None)
        logging.info(
            "%s grid: size=(%s x %s), cell=%.6f x %.6f, SR=%s",
            tag,
            getattr(d, "width", "?"),
            getattr(d, "height", "?"),
            getattr(d, "meanCellWidth", -1.0),
            getattr(d, "meanCellHeight", -1.0),
            getattr(cs, "name", "Unknown")
        )
    except Exception:
        pass

def _warn_if_misaligned(tcc_path, ref_path):
    try:
        dt = arcpy.Describe(tcc_path); dr = arcpy.Describe(ref_path)
        sr_t = getattr(dt, "spatialReference", None)
        sr_r = getattr(dr, "spatialReference", None)
        cw_t, ch_t = dt.meanCellWidth, dt.meanCellHeight
        cw_r, ch_r = dr.meanCellWidth, dr.meanCellHeight
        if (not sr_t or not sr_r) or (sr_t.name != sr_r.name) or (abs(cw_t - cw_r) > 1e-6) or (abs(ch_t - ch_r) > 1e-6):
            logging.warning(
                "Potential on-the-fly reprojection/resampling:\n  TCC=%s (cell %.6f x %.6f, SR=%s)\n  REF=%s (cell %.6f x %.6f, SR=%s)",
                tcc_path, cw_t, ch_t, getattr(sr_t, "name", "?"),
                ref_path, cw_r, ch_r, getattr(sr_r, "name", "?"),
            )
    except Exception:
        pass

def main():
    logging.info("Starting NLCD TCC ABSOLUTE (pp) change processing.")
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

        # Log reference grid
        _log_grid_info("REF", cfg.NLCD_RASTER)

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

            out_change   = os.path.join(cfg.NLCD_TCC_CHANGE_DIR,      f"nlcd_tcc_change_{pname}.tif")
            out_severity = os.path.join(cfg.NLCD_HARVEST_SEVERITY_DIR, f"nlcd_tcc_severity_{pname}.tif")

            write_change = getattr(cfg, "WRITE_PP_CHANGE", True)
            expected = [out_severity] + ([out_change] if write_change else [])
            if all(_exists(p) for p in expected):
                logging.info("Outputs already exist for %s; skipping.", pname)
                continue

            # Warn if TCC inputs are misaligned with reference (expensive reprojection risk)
            _warn_if_misaligned(sp, cfg.NLCD_RASTER)
            _warn_if_misaligned(ep, cfg.NLCD_RASTER)

            # ---------- FAST PATH: if change exists and we only need severity ----------
            dpp = None
            if USE_EXISTING_PP_CHANGE_FOR_SEVERITY and _exists(out_change):
                t0 = time.perf_counter()
                dpp = Raster(out_change)  # reuse existing change
                logging.info("Loaded existing pp change for %s in %.1fs", pname, time.perf_counter() - t0)

            if dpp is None:
                # ---------- Compute dpp (absolute pp change) ----------
                t0 = time.perf_counter()
                start_r, end_r = Raster(sp), Raster(ep)
                inv_s = IsNull(start_r) | (start_r < 0) | (start_r > 100)
                inv_e = IsNull(end_r)   | (end_r   < 0) | (end_r   > 100)
                s_ok  = SetNull(inv_s, start_r)
                e_ok  = SetNull(inv_e, end_r)

                dpp = SetNull(IsNull(s_ok) | IsNull(e_ok), e_ok - s_ok)
                dpp = Con(dpp < -100, -100, Con(dpp > 100, 100, dpp))
                logging.info("Computed dpp for %s in %.1f min", pname, (time.perf_counter() - t0) / 60.0)

                if write_change:
                    t1 = time.perf_counter()
                    if _save_tif(Int(dpp), out_change, "16_BIT_SIGNED", lzw=True):
                        logging.info("Saved pp change (Int16 LZW) => %s (%.1f s)", out_change, time.perf_counter() - t1)
                else:
                    logging.info("WRITE_PP_CHANGE=False; skipping write of %s", out_change)

            # ---------- Loss magnitude (integer) ----------
            t2 = time.perf_counter()
            # If dpp is float, cast to Int after Abs for safety
            loss = Con(dpp < 0, Abs(dpp), 0)
            loss_int = Int(loss)  # 0..100
            logging.info("Prepared loss_int for %s in %.1f s", pname, time.perf_counter() - t2)

            # ---------- Classify via Reclassify (faster than nested Con) ----------
            t3 = time.perf_counter()
            sev = _classify_loss_fast(loss_int)
            logging.info("Classified severity for %s in %.1f s", pname, time.perf_counter() - t3)

            # ---------- Save severity (8-bit, LZW) ----------
            t4 = time.perf_counter()
            if _save_tif(sev, out_severity, "8_BIT_UNSIGNED", lzw=True):
                logging.info("Saved pp-based severity => %s (%.1f s)", out_severity, time.perf_counter() - t4)

        logging.info("Completed ABSOLUTE (pp) change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")

if __name__ == "__main__":
    main()
