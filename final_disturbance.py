# final_disturbance.py
"""
Combines fire, insect, and harvest rasters via CellStatistics (MAX).
Builds final outputs for BOTH harvest methods (absolute & percent) in one run,
writing compressed (LZW) GeoTIFFs into the centralized NLCD_harvest_severity
folder structure.

Final codes:
  1–4 : Harvest severity (as provided by the chosen harvest workflow; Hansen is binary=1)
  5   : Insect/Disease presence  (presence -> 5, else 0)
  10  : Fire presence            (presence -> 10, else 0; low fire severity masked pre-combine if enabled)

Also exports a “harvest_counted” layer per method:
  harvest_counted_{method_tag}_{period}.tif
= Harvest (1–4) after masking out any fire/insect (keeps only what will be counted as harvest).
"""

import os
import time
import logging
import arcpy
from arcpy.sa import *  # noqa
import disturbance_config as cfg

# Which harvest workflows to build finals for (defaults to abs & percent)
FINAL_HARVEST_WORKFLOWS = getattr(
    cfg, "FINAL_HARVEST_WORKFLOWS",
    ["nlcd_tcc_severity", "nlcd_tcc_percent_severity"]
)

def _exists(p): return bool(p) and (arcpy.Exists(p) or os.path.exists(p))

def _set_env_from_dataset(ds):
    if not _exists(ds):
        raise FileNotFoundError(f"Cannot set env; dataset not found: {ds}")
    d = arcpy.Describe(ds)
    arcpy.env.snapRaster = ds
    arcpy.env.cellSize = ds
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference
    logging.info("ArcPy env initialized from: %s", ds)

def _save_byte_tif(ras, out_tif, *, overwrite=False, lzw=True):
    if not overwrite and _exists(out_tif):
        logging.info("Skipping existing output: %s", out_tif)
        return False
    try:
        if arcpy.Exists(out_tif):
            arcpy.management.Delete(out_tif)
    except Exception:
        pass
    if lzw:
        with arcpy.EnvManager(compression="LZW", pyramid="NONE"):
            arcpy.management.CopyRaster(ras, out_tif, pixel_type="8_BIT_UNSIGNED", format="TIFF")
    else:
        with arcpy.EnvManager(pyramid="NONE"):
            arcpy.management.CopyRaster(ras, out_tif, pixel_type="8_BIT_UNSIGNED", format="TIFF")
    if getattr(cfg, "COMPUTE_OUTPUT_STATS", False):
        try:
            arcpy.management.CalculateStatistics(out_tif)
        except Exception:
            pass
    return True

def _mask_low_severity_fire(fire_ras: Raster) -> Raster:
    mask_fire = getattr(cfg, "MASK_LOW_SEVERITY_FIRE", True)
    low_code = getattr(cfg, "FIRE_LOW_SEVERITY_CODE", 3)
    if not mask_fire:
        logging.info("MASK_LOW_SEVERITY_FIRE is False; keeping all fire classes as-is.")
        return fire_ras
    logging.info("Masking low-severity fire: code %s -> 0 (pre-combine).", low_code)
    return Con(fire_ras == low_code, 0, fire_ras)

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

def _warn_if_misaligned(path, ref_path, label):
    try:
        dp = arcpy.Describe(path); dr = arcpy.Describe(ref_path)
        sr_p = getattr(dp, "spatialReference", None)
        sr_r = getattr(dr, "spatialReference", None)
        cw_p, ch_p = dp.meanCellWidth, dp.meanCellHeight
        cw_r, ch_r = dr.meanCellWidth, dr.meanCellHeight
        if (not sr_p or not sr_r) or (sr_p.name != sr_r.name) or (abs(cw_p - cw_r) > 1e-6) or (abs(ch_p - ch_r) > 1e-6):
            logging.warning(
                "Potential on-the-fly reprojection/resampling for %s:\n  %s (cell %.6f x %.6f, SR=%s)\n  REF=%s (cell %.6f x %.6f, SR=%s)",
                label, path, cw_p, ch_p, getattr(sr_p, "name", "?"),
                ref_path, cw_r, ch_r, getattr(sr_r, "name", "?"),
            )
    except Exception:
        pass

def main():
    logging.info("Starting final_disturbance.py... (building: %s)", ", ".join(FINAL_HARVEST_WORKFLOWS))

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.pyramid = "NONE"
    arcpy.env.parallelProcessingFactor = getattr(cfg, "PARALLEL_PROCESSING_FACTOR", "90%")
    if getattr(cfg, "SCRATCH_WORKSPACE", ""):
        arcpy.env.scratchWorkspace = cfg.SCRATCH_WORKSPACE
        arcpy.env.workspace = cfg.SCRATCH_WORKSPACE

    if not _exists(cfg.NLCD_RASTER):
        raise FileNotFoundError(f"NLCD_RASTER not found: {cfg.NLCD_RASTER}")
    _set_env_from_dataset(cfg.NLCD_RASTER)
    _log_grid_info("REF", cfg.NLCD_RASTER)

    final_out_dir = cfg.final_combined_dir()  # same centralized folder for all methods
    os.makedirs(final_out_dir, exist_ok=True)
    logging.info("Final combined rasters directory => %s", final_out_dir)

    # Summary accumulators
    processed = {wf: [] for wf in FINAL_HARVEST_WORKFLOWS}
    skipped   = {wf: [] for wf in FINAL_HARVEST_WORKFLOWS}

    for period in cfg.TIME_PERIODS_ALL.keys():
        # Common inputs for all methods
        fire_path   = os.path.join(cfg.FIRE_OUTPUT_DIR,   f"fire_{period}.tif")
        insect_path = os.path.join(cfg.INSECT_FINAL_DIR,  f"insect_damage_{period}.tif")

        if not (_exists(fire_path) and _exists(insect_path)):
            logging.error("Skipping all methods for %s — missing common inputs (fire/insect).", period)
            logging.warning("  fire  : %s [%s]", fire_path, "OK" if _exists(fire_path) else "MISSING")
            logging.warning("  insect: %s [%s]", insect_path, "OK" if _exists(insect_path) else "MISSING")
            for wf in FINAL_HARVEST_WORKFLOWS:
                skipped[wf].append((period, "missing fire/insect"))
            continue

        # Misalignment diagnostics for common layers
        _warn_if_misaligned(fire_path,   cfg.NLCD_RASTER, "fire")
        _warn_if_misaligned(insect_path, cfg.NLCD_RASTER, "insect")

        # Load & prepare fire/insect once per period
        t0 = time.perf_counter()
        fire_ras   = Raster(fire_path)
        insect_ras = Raster(insect_path)
        logging.info("Loaded fire/insect for %s in %.1f s", period, time.perf_counter() - t0)

        # Fire presence: mask low-sev fire, recode presence -> 10
        t1 = time.perf_counter()
        fire_masked = _mask_low_severity_fire(fire_ras)
        fire_final  = Con(fire_masked > 0, 10, 0)
        logging.info("Prepared fire presence for %s in %.1f s", period, time.perf_counter() - t1)

        # Insect presence: presence -> 5
        t2 = time.perf_counter()
        insect_final = Con(insect_ras > 0, 5, 0)
        logging.info("Prepared insect presence for %s in %.1f s", period, time.perf_counter() - t2)

        # Save presence exports (shared by both methods) if missing
        out_insect = os.path.join(cfg.NLCD_FINAL_INSECT_DIR, f"insect_{period}.tif")
        out_fire   = os.path.join(cfg.NLCD_FINAL_FIRE_DIR,   f"fire_{period}.tif")
        if _save_byte_tif(insect_final, out_insect, lzw=True):
            logging.info("Saved insect presence => %s", out_insect)
        if _save_byte_tif(fire_final, out_fire, lzw=True):
            logging.info("Saved fire presence => %s", out_fire)

        # Now build finals for each requested harvest workflow
        for wf in FINAL_HARVEST_WORKFLOWS:
            hcfg = cfg.harvest_product_config(wf)
            method_tag = hcfg.get("method_tag", "abs")  # 'abs' (or 'hansen' if used)
            harvest_path = cfg.harvest_raster_path(period, workflow=wf)

            if not _exists(harvest_path):
                logging.error("Skipping %s for %s — missing harvest raster: %s", wf, period, harvest_path)
                skipped[wf].append((period, "missing harvest"))
                continue

            _warn_if_misaligned(harvest_path, cfg.NLCD_RASTER, f"harvest ({method_tag})")

            # Output paths for this method
            out_combined     = os.path.join(final_out_dir,                   f"disturb_{method_tag}_{period}.tif")
            out_harvest_only = os.path.join(cfg.NLCD_FINAL_HARVEST_ONLY_DIR, f"harvest_counted_{method_tag}_{period}.tif")

            need_combined     = not _exists(out_combined)
            need_harvest_only = not _exists(out_harvest_only)

            if not (need_combined or need_harvest_only):
                logging.info("Finals already exist for %s (%s); skipping.", period, method_tag)
                skipped[wf].append((period, "existing outputs"))
                continue

            harvest_ras = Raster(harvest_path)

            # Combined MAX (DATA): [fire=10, insect=5, harvest=1..4]
            if need_combined:
                t3 = time.perf_counter()
                combined_max = CellStatistics([fire_final, insect_final, harvest_ras], "MAXIMUM", "DATA")
                logging.info("Computed combined MAX for %s (%s) in %.1f s", period, method_tag, time.perf_counter() - t3)
                t4 = time.perf_counter()
                if _save_byte_tif(combined_max, out_combined, lzw=True):
                    logging.info("Final combined disturbance => %s (%.1f s)", out_combined, time.perf_counter() - t4)

            # What counts as harvest after masking out fire/insect (keep only 1..4 where fire/insect == 0)
            if need_harvest_only:
                t5 = time.perf_counter()
                harvest_counted = Con((fire_final > 0) | (insect_final > 0), 0, harvest_ras)
                logging.info("Computed harvest_counted for %s (%s) in %.1f s", period, method_tag, time.perf_counter() - t5)
                t6 = time.perf_counter()
                if _save_byte_tif(harvest_counted, out_harvest_only, lzw=True):
                    logging.info("Harvest counted (masked by fire/insect) => %s (%.1f s)", out_harvest_only, time.perf_counter() - t6)

            processed[wf].append(period)

    # -------- Summary --------
    for wf in FINAL_HARVEST_WORKFLOWS:
        ok = processed[wf]; sk = skipped[wf]
        logging.info("Workflow '%s' summary -> processed: %d, skipped: %d", wf, len(ok), len(sk))
        if ok:
            logging.info("  Processed periods: %s", ", ".join(ok))
        for p, reason in sk:
            logging.info("  Skipped %s (%s)", p, reason)

    arcpy.CheckInExtension("Spatial")
    logging.info("final_disturbance.py completed.")

if __name__ == "__main__":
    main()