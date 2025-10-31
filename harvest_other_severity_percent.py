#!/usr/bin/env python3
"""
NLCD TCC — PERCENT change and severity
--------------------------------------
For each period {start,end}:
  1) Validate inputs to 0–100 and mask NoData; also require start > min_base.
  2) Compute percent change: pct = 100 * (end - start) / start  (clip to [-100, 100]).
     FAST PATH: If the percent-change TIFF already exists, reuse it instead of recomputing.
     (If the existing raster is scaled Int16, we convert it back to float percent.)
  3) Derive loss magnitude only: loss = |pct| where pct < 0 else 0.
  4) Classify loss to severity 0–4 using cfg.NLCD_TCC_PCT_SEVERITY_BREAKS (or pp breaks if not set),
     via Reclassify on an Int(loss) raster for better performance.

Outputs:
  - {NLCD_TCC_PCT_CHANGE_DIR}/nlcd_tcc_pct_change_{period}.tif         [Float32 or scaled Int16 (0.1% units)]
  - {NLCD_HARVEST_PCT_SEV_DIR}/nlcd_tcc_pct_severity_{period}.tif      [8-bit, LZW]
"""

import os
import time
import logging
import arcpy
from arcpy.sa import Abs, Con, Float, Raster, SetNull, IsNull, Int, Reclassify, RemapRange  # noqa
import disturbance_config as cfg

# ---------------------------------------------------------------------
# Toggles / conventions (match practices in harvest_other_severity)
# ---------------------------------------------------------------------

# Optional fast-path reuse for classification if a percent-change raster already exists
USE_EXISTING_PCT_CHANGE_FOR_SEVERITY = getattr(cfg, "USE_EXISTING_PCT_CHANGE_FOR_SEVERITY", True)

# Whether to persist the percent-change raster (change layer) alongside severity
WRITE_PCT_CHANGE = getattr(cfg, "WRITE_PCT_CHANGE", True)

# Store percent-change as scaled Int16 (e.g., 0.1% units) to reduce size; otherwise write Float32
SAVE_PCT_AS_INT16 = getattr(cfg, "SAVE_PCT_AS_INT16", False)
PCT_SCALE = getattr(cfg, "PCT_SCALE", 10)  # -100..100% -> -1000..1000 (0.1% units)

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
    # Use LZW like the pp script (faster end-to-end for small-range rasters)
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

def _pct_breaks():
    # If percent breaks aren't set, fall back to the pp breaks (same default [25, 50, 75, 100])
    return getattr(cfg, "NLCD_TCC_PCT_SEVERITY_BREAKS", cfg.NLCD_TCC_SEVERITY_BREAKS)

def _classify_loss_fast(loss_int):
    """
    Reclassify integer loss magnitude (percent) to severity 0..4 using RemapRange.
    Assumes loss_int is Int in [0..100] with NoData outside valid areas.
    """
    b1, b2, b3, b4 = sorted(_pct_breaks())
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

def _as_percent_float(r):
    """Return a Float raster in percent units even if the source is scaled Int16."""
    try:
        px = arcpy.Describe(r).pixelType  # 'F32','F64','S16','U16','S32','U32','U8',...
    except Exception:
        px = None
    if px in ("F32", "F64", None):
        return r  # already float percent
    # Integer percent raster encountered
    if SAVE_PCT_AS_INT16:
        # Convert scaled INT back to float percent using PCT_SCALE
        return Float(r) / float(PCT_SCALE)
    else:
        logging.warning("Integer percent-change raster encountered but SAVE_PCT_AS_INT16=False; "
                        "interpreting as raw percent integers.")
        return Float(r)

def main():
    logging.info("Starting NLCD TCC PERCENT change processing.")
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

        # Log reference grid, like the pp script
        _log_grid_info("REF", cfg.NLCD_RASTER)

        min_base = getattr(cfg, "NLCD_TCC_PCT_MIN_BASE", 0)  # require start > min_base for percent math

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

            out_change   = os.path.join(cfg.NLCD_TCC_PCT_CHANGE_DIR, f"nlcd_tcc_pct_change_{pname}.tif")
            out_severity = os.path.join(cfg.NLCD_HARVEST_PCT_SEV_DIR,  f"nlcd_tcc_pct_severity_{pname}.tif")

            expected = [out_severity] + ([out_change] if WRITE_PCT_CHANGE else [])
            if all(_exists(p) for p in expected):
                logging.info("Outputs already exist for %s; skipping.", pname)
                continue

            # Warn if TCC inputs are misaligned with reference (expensive reprojection risk)
            _warn_if_misaligned(sp, cfg.NLCD_RASTER)
            _warn_if_misaligned(ep, cfg.NLCD_RASTER)

            # ---------- FAST PATH: reuse existing percent-change if present ----------
            pct = None
            if USE_EXISTING_PCT_CHANGE_FOR_SEVERITY and _exists(out_change):
                t0 = time.perf_counter()
                pct = Raster(out_change)
                # Ensure we have Float percent units even if saved earlier as scaled Int16
                pct = _as_percent_float(pct)
                logging.info("Loaded existing percent change for %s in %.1fs", pname, time.perf_counter() - t0)

            if pct is None:
                # ---------- Compute percent change ----------
                t0 = time.perf_counter()
                start_r, end_r = Raster(sp), Raster(ep)

                # Validity: keep 0..100 & NoData; also require start > min_base to avoid division instability
                inv_s = IsNull(start_r) | (start_r < 0) | (start_r > 100)
                inv_e = IsNull(end_r)   | (end_r   < 0) | (end_r   > 100)
                s_small = start_r <= min_base

                s_ok = SetNull(inv_s | s_small, start_r)
                e_ok = SetNull(inv_e,          end_r)

                pct = SetNull(IsNull(s_ok) | IsNull(e_ok), Float((e_ok - s_ok) / s_ok * 100))
                pct = Con(pct < -100, -100, Con(pct > 100, 100, pct))
                logging.info("Computed percent change for %s in %.1f min", pname, (time.perf_counter() - t0) / 60.0)

                if WRITE_PCT_CHANGE:
                    t1 = time.perf_counter()
                    if SAVE_PCT_AS_INT16:
                        # Save scaled Int16 (0.1% units by default)
                        if _save_tif(Int(pct * PCT_SCALE), out_change, "16_BIT_SIGNED", lzw=True):
                            logging.info("Saved percent change (Int16 LZW, scale=%s) => %s (%.1f s)",
                                         PCT_SCALE, out_change, time.perf_counter() - t1)
                    else:
                        if _save_tif(pct, out_change, "32_BIT_FLOAT", lzw=True):
                            logging.info("Saved percent change (Float32 LZW) => %s (%.1f s)",
                                         out_change, time.perf_counter() - t1)
                else:
                    logging.info("WRITE_PCT_CHANGE=False; skipping write of %s", out_change)

            # ---------- Loss magnitude (integer) ----------
            t2 = time.perf_counter()
            loss = Con(pct < 0, Abs(pct), 0)   # keep loss only
            loss_int = Int(loss)               # cast to Int for faster Reclassify
            logging.info("Prepared loss_int for %s in %.1f s", pname, time.perf_counter() - t2)

            # ---------- Classify via Reclassify (fast) ----------
            t3 = time.perf_counter()
            sev = _classify_loss_fast(loss_int)
            logging.info("Classified percent-based severity for %s in %.1f s", pname, time.perf_counter() - t3)

            # ---------- Save severity (8-bit, LZW) ----------
            t4 = time.perf_counter()
            if _save_tif(sev, out_severity, "8_BIT_UNSIGNED", lzw=True):
                logging.info("Saved percent-based severity => %s (%.1f s)", out_severity, time.perf_counter() - t4)

        logging.info("Completed PERCENT change processing.")
    finally:
        arcpy.CheckInExtension("Spatial")

if __name__ == "__main__":
    main()
