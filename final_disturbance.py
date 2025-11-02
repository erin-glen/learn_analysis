# final_disturbance.py
"""
Combines fire, insect, and harvest rasters via CellStatistics (MAX).
Saves to the centralized NLCD_harvest_severity structure with LZW compression.

Final codes:
  1–4 : Harvest severity (as provided by the chosen harvest workflow)
  5   : Insect/Disease presence (presence -> 5, else 0)
  10  : Fire presence (after masking low-severity fire code if enabled) (presence -> 10, else 0)

Also exports a “harvest_counted” layer = harvest after masking out any fire/insect.
"""

import os
import time
import logging
import arcpy
from arcpy.sa import *  # noqa
import disturbance_config as cfg

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
    logging.info("Starting final_disturbance.py...")

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

    hcfg = cfg.harvest_product_config()
    method_tag = hcfg.get("method_tag", "abs")
    logging.info("Using harvest workflow '%s' => %s", cfg.HARVEST_WORKFLOW, hcfg.get("description", ""))

    final_out_dir = cfg.final_combined_dir()
    os.makedirs(final_out_dir, exist_ok=True)
    logging.info("Final combined rasters => %s", final_out_dir)

    processed, skipped = [], []

    for period in cfg.TIME_PERIODS.keys():
        fire_path   = os.path.join(cfg.FIRE_OUTPUT_DIR,   f"fire_{period}.tif")
        insect_path = os.path.join(cfg.INSECT_FINAL_DIR,  f"insect_damage_{period}.tif")
        harvest_path= cfg.harvest_raster_path(period)

        inputs = {"fire": fire_path, "insect": insect_path, "harvest": harvest_path}
        missing = [k for k, v in inputs.items() if not _exists(v)]
        if missing:
            lines = [f"Input status for period={period}:"]
            for name, p in inputs.items():
                lines.append(f"  - {name:<7}: {p} [{'OK' if _exists(p) else 'MISSING'}]")
            logging.warning("\n".join(lines))
            logging.error("Skipping period=%s due to missing inputs: %s", period, ", ".join(missing))
            skipped.append({"period": period, "reason": "missing inputs", "details": missing})
            continue

        # Misalignment diagnostics (helps avoid hidden reprojection costs)
        _warn_if_misaligned(fire_path,    cfg.NLCD_RASTER, "fire")
        _warn_if_misaligned(insect_path,  cfg.NLCD_RASTER, "insect")
        _warn_if_misaligned(harvest_path, cfg.NLCD_RASTER, "harvest")

        # Build output paths
        out_combined     = os.path.join(final_out_dir,                        f"disturb_{method_tag}_{period}.tif")
        out_harvest_only = os.path.join(cfg.NLCD_FINAL_HARVEST_ONLY_DIR,      f"harvest_counted_{method_tag}_{period}.tif")
        out_insect       = os.path.join(cfg.NLCD_FINAL_INSECT_DIR,            f"insect_{period}.tif")
        out_fire         = os.path.join(cfg.NLCD_FINAL_FIRE_DIR,              f"fire_{period}.tif")

        need_combined     = not _exists(out_combined)
        need_harvest_only = not _exists(out_harvest_only)
        need_insect       = not _exists(out_insect)
        need_fire         = not _exists(out_fire)

        if not (need_combined or need_harvest_only or need_insect or need_fire):
            logging.info("All final disturbance outputs already exist for %s; skipping.", period)
            skipped.append({"period": period, "reason": "existing outputs",
                            "details": [out_combined, out_harvest_only, out_insect, out_fire]})
            continue

        # Load inputs once
        t0 = time.perf_counter()
        fire_ras   = Raster(fire_path)     if (need_combined or need_fire or need_harvest_only) else None
        insect_ras = Raster(insect_path)   if (need_combined or need_insect or need_harvest_only) else None
        harvest_ras= Raster(harvest_path)  if (need_combined or need_harvest_only) else None
        logging.info("Loaded inputs for %s in %.1f s", period, time.perf_counter() - t0)

        # Fire presence (10) and masking of low-severity fire (pre-combine)
        fire_final = None
        if (need_combined or need_fire or need_harvest_only) and fire_ras is not None:
            t1 = time.perf_counter()
            fire_masked = _mask_low_severity_fire(fire_ras)
            fire_final  = Con(fire_masked > 0, 10, 0)
            logging.info("Prepared fire presence for %s in %.1f s", period, time.perf_counter() - t1)

        # Insect presence (5)
        insect_final = None
        if (need_combined or need_insect or need_harvest_only) and insect_ras is not None:
            t2 = time.perf_counter()
            insect_final = Con(insect_ras > 0, 5, 0)
            logging.info("Prepared insect presence for %s in %.1f s", period, time.perf_counter() - t2)

        wrote_any = False

        # Combined MAX only if needed
        if need_combined:
            t3 = time.perf_counter()
            combined_max = CellStatistics([r for r in (fire_final, insect_final, harvest_ras) if r is not None],
                                          "MAXIMUM", "DATA")
            logging.info("Computed combined MAX for %s in %.1f s", period, time.perf_counter() - t3)
            t4 = time.perf_counter()
            if _save_byte_tif(combined_max, out_combined, lzw=True):
                logging.info("Final combined disturbance => %s (%.1f s)", out_combined, time.perf_counter() - t4)
                wrote_any = True

        # Harvest counted (mask out any fire/insect)
        if need_harvest_only:
            t5 = time.perf_counter()
            harvest_counted = Con(((fire_final if fire_final is not None else 0) > 0) |
                                  ((insect_final if insect_final is not None else 0) > 0),
                                  0, harvest_ras)
            logging.info("Computed harvest_counted for %s in %.1f s", period, time.perf_counter() - t5)
            t6 = time.perf_counter()
            if _save_byte_tif(harvest_counted, out_harvest_only, lzw=True):
                logging.info("Harvest counted (masked by fire/insect) => %s (%.1f s)",
                             out_harvest_only, time.perf_counter() - t6)
                wrote_any = True

        # Convenience presence exports
        if need_insect and insect_final is not None:
            t7 = time.perf_counter()
            if _save_byte_tif(insect_final, out_insect, lzw=True):
                logging.info("Saved insect presence => %s (%.1f s)", out_insect, time.perf_counter() - t7)
                wrote_any = True

        if need_fire and fire_final is not None:
            t8 = time.perf_counter()
            if _save_byte_tif(fire_final, out_fire, lzw=True):
                logging.info("Saved fire presence => %s (%.1f s)", out_fire, time.perf_counter() - t8)
                wrote_any = True

        if wrote_any:
            processed.append(period)
        else:
            skipped.append({"period": period, "reason": "existing outputs",
                            "details": [out_combined, out_harvest_only, out_insect, out_fire]})

    logging.info("Run summary -> processed: %d, skipped: %d", len(processed), len(skipped))
    if processed:
        logging.info("Processed periods: %s", ", ".join(processed))
    if skipped:
        for item in skipped:
            reason = item.get("reason", "unknown reason")
            period = item.get("period")
            details = item.get("details")
            if reason == "missing inputs" and details:
                logging.warning("Skipped %s (missing: %s)", period, ", ".join(details))
            elif reason == "existing outputs":
                logging.info("Skipped %s (all outputs already exist)", period)
            else:
                logging.info("Skipped %s (%s)", period, reason)

    arcpy.CheckInExtension("Spatial")
    logging.info("final_disturbance.py completed.")

if __name__ == "__main__":
    main()
