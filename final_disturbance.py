# final_disturbance.py
"""
Combines fire, insect, and harvest rasters via CellStatistics (MAX).
Saves to a centralized NLCD_harvest_severity folder structure without compression.

Final codes:
  1–4 : Harvest severity (as provided by the chosen harvest workflow)
  5   : Insect/Disease presence
  10  : Fire presence (after masking low-severity fire code if enabled)

Also exports a “harvest_counted” layer = harvest after masking out any fire/insect.
"""

import os
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

def _save_byte_tif(ras, out_tif):
    try:
        if arcpy.Exists(out_tif):
            arcpy.management.Delete(out_tif)
    except Exception:
        pass
    arcpy.management.CopyRaster(ras, out_tif, pixel_type="8_BIT_UNSIGNED", format="TIFF")
    if getattr(cfg, "COMPUTE_OUTPUT_STATS", False):
        try:
            arcpy.management.CalculateStatistics(out_tif)
        except Exception:
            pass

def _mask_low_severity_fire(fire_ras: Raster) -> Raster:
    mask_fire = getattr(cfg, "MASK_LOW_SEVERITY_FIRE", True)
    low_code = getattr(cfg, "FIRE_LOW_SEVERITY_CODE", 3)
    if not mask_fire:
        logging.info("MASK_LOW_SEVERITY_FIRE is False; keeping all fire classes as-is.")
        return fire_ras
    logging.info("Masking low-severity fire: code %s -> 0 (pre-combine).", low_code)
    return Con(fire_ras == low_code, 0, fire_ras)

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
            skipped.append((period, missing))
            continue

        fire_ras   = Raster(fire_path)
        insect_ras = Raster(insect_path)
        harvest_ras= Raster(harvest_path)  # already 0–4 harvest severity

        # Fire: drop low-sev if configured, then recode presence -> 10
        fire_masked = _mask_low_severity_fire(fire_ras)
        fire_final  = Con(fire_masked > 0, 10, 0)

        # Insect: presence -> 5 (treat any nonzero as insect presence)
        insect_final = Con(insect_ras > 0, 5, 0)

        # Combined MAX (DATA) using standardized codes
        combined_max = CellStatistics([fire_final, insect_final, harvest_ras], "MAXIMUM", "DATA")

        # Save combined
        out_combined = os.path.join(final_out_dir, f"disturb_{method_tag}_{period}.tif")
        _save_byte_tif(combined_max, out_combined)
        logging.info("Final combined disturbance => %s", out_combined)

        # Export “what counts as harvest” after masking out fire/insect:
        # keep harvest 1–4 only where fire==0 and insect==0
        harvest_counted = Con((fire_final > 0) | (insect_final > 0), 0, harvest_ras)
        out_harvest_only = os.path.join(cfg.NLCD_FINAL_HARVEST_ONLY_DIR, f"harvest_counted_{method_tag}_{period}.tif")
        _save_byte_tif(harvest_counted, out_harvest_only)
        logging.info("Harvest counted (masked by fire/insect) => %s", out_harvest_only)

        # Convenience exports of presence layers
        out_insect = os.path.join(cfg.NLCD_FINAL_INSECT_DIR, f"insect_{period}.tif")
        _save_byte_tif(insect_final, out_insect)

        out_fire = os.path.join(cfg.NLCD_FINAL_FIRE_DIR, f"fire_{period}.tif")
        _save_byte_tif(fire_final, out_fire)

        processed.append(period)

    logging.info("Run summary -> processed: %d, skipped: %d", len(processed), len(skipped))
    if processed:
        logging.info("Processed periods: %s", ", ".join(processed))
    if skipped:
        for period, miss in skipped:
            logging.warning("Skipped %s (missing: %s)", period, ", ".join(miss))

    arcpy.CheckInExtension("Spatial")
    logging.info("final_disturbance.py completed.")

if __name__ == "__main__":
    main()
