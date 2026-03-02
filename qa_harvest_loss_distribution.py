#!/usr/bin/env python3
"""
qa_harvest_loss_distribution.py
------------------------------------------------------------
Purpose
  Investigate the distribution of NLCD TCC loss magnitudes (percentage-point loss)
  underlying the harvest severity outputs, with emphasis on the lowest harvest class.

Key fix vs prior versions
  This script uses TabulateArea (NOT ZonalHistogram) to preserve exact integer loss
  values (0..100). ZonalHistogram can bin values (e.g., 256 bins) when there are many
  unique values, which makes "loss <= 5pp" and threshold tests appear as ~0.

Optional masking
  By default, the script auto-builds a per-period AOI mask from NLCD land cover:
  cells must be forest in both period endpoints ("forest remains forest").
  You can pass --mask to override with a manual AOI (feature or raster).
  If your mask raster is 0/1 (0 outside), use --mask0-outside to treat 0 as NoData outside.

Notes
  - Requires Spatial Analyst.
  - Outputs area in hectares using reference raster cell size.
"""

from __future__ import annotations

import os
import re
import csv
import time
import argparse
import logging
from typing import Dict, List, Tuple, Optional

import arcpy
from arcpy.sa import Raster, Con, Abs, Int, IsNull, SetNull, TabulateArea

import disturbance_config as cfg


# ---------------------------
# Helpers
# ---------------------------

def _exists(p: str) -> bool:
    return bool(p) and (arcpy.Exists(p) or os.path.exists(p))


def _parse_period(period: str) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d{4})_(\d{4})\s*$", period)
    if not m:
        raise ValueError(f"Unrecognized period name '{period}'. Expected like '2013_2016'.")
    return int(m.group(1)), int(m.group(2))


def _setup_logging(level: str = "INFO", log_file: str = "") -> None:
    """
    Ensure we have a console handler (and optional file handler), even if cfg already configured logging.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(lvl)
        root.addHandler(sh)
    else:
        # Update formatter/level on existing stream handlers
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(fmt)
                h.setLevel(lvl)

    if log_file:
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        root.addHandler(fh)


def _set_env_from_reference(ref_raster: str) -> None:
    d = arcpy.Describe(ref_raster)
    arcpy.env.snapRaster = ref_raster
    arcpy.env.cellSize = ref_raster
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference
    arcpy.env.pyramid = "NONE"
    arcpy.env.parallelProcessingFactor = getattr(cfg, "PARALLEL_PROCESSING_FACTOR", "90%")
    arcpy.env.overwriteOutput = True

    if getattr(cfg, "SCRATCH_WORKSPACE", ""):
        arcpy.env.scratchWorkspace = cfg.SCRATCH_WORKSPACE
        arcpy.env.workspace = cfg.SCRATCH_WORKSPACE


def _cell_area_map2_and_ha_from_reference(ref_raster: str) -> Tuple[float, float]:
    d = arcpy.Describe(ref_raster)
    cw = float(getattr(d, "meanCellWidth", 0.0))
    ch = float(getattr(d, "meanCellHeight", 0.0))
    cell_area_map2 = abs(cw * ch)

    sr = getattr(d, "spatialReference", None)
    unit = getattr(sr, "linearUnitName", "Unknown") if sr else "Unknown"
    if unit.lower() not in ("meter", "metre"):
        logging.warning(
            "Reference raster linear units appear to be '%s'. "
            "Area conversion to hectares assumes meters. Verify if results look odd.",
            unit
        )
    cell_area_ha = cell_area_map2 / 10000.0
    return cell_area_map2, cell_area_ha


def _safe_raster_prop(ras, prop: str) -> Optional[float]:
    try:
        out = arcpy.management.GetRasterProperties(ras, prop).getOutput(0)
        if out in (None, "", " "):
            return None
        return float(out)
    except Exception:
        return None


def _resolve_zone_raster(period: str, workflow: str, prefer_harvest_counted: bool = True) -> Tuple[str, str]:
    """
    Returns (zone_path, zone_label).
    Prefer harvest_counted (post fire/insect masking) when present because that's
    closest to what LEARN should be counting as harvest.
    """
    hcfg = cfg.harvest_product_config(workflow)
    method_tag = hcfg.get("method_tag", "abs")

    counted = os.path.join(cfg.NLCD_FINAL_HARVEST_ONLY_DIR, f"harvest_counted_{method_tag}_{period}.tif")
    severity = cfg.harvest_raster_path(period, workflow=workflow)  # nlcd_tcc_severity_{period}.tif

    if prefer_harvest_counted and _exists(counted):
        return counted, f"harvest_counted_{method_tag}"
    if _exists(severity):
        return severity, "severity_0to4"
    if _exists(counted):
        return counted, f"harvest_counted_{method_tag}"

    raise FileNotFoundError(
        f"No zone raster found for {period}. Expected either:\n"
        f"  - {counted}\n"
        f"  - {severity}"
    )


def _compute_dpp_from_tcc(period: str, start_year: int, end_year: int) -> Raster:
    sp = cfg.NLCD_TCC_RASTERS.get(start_year)
    ep = cfg.NLCD_TCC_RASTERS.get(end_year)
    if not (sp and ep and os.path.exists(sp) and os.path.exists(ep)):
        raise FileNotFoundError(
            f"Cannot recompute dpp for {period}: missing TCC inputs for {start_year} or {end_year}.\n"
            f"  start={sp}\n"
            f"  end  ={ep}"
        )

    start_r = Raster(sp)
    end_r   = Raster(ep)

    inv_s = IsNull(start_r) | (start_r < 0) | (start_r > 100)
    inv_e = IsNull(end_r)   | (end_r   < 0) | (end_r   > 100)

    s_ok = SetNull(inv_s, start_r)
    e_ok = SetNull(inv_e, end_r)

    dpp = SetNull(IsNull(s_ok) | IsNull(e_ok), e_ok - s_ok)
    dpp = Con(dpp < -100, -100, Con(dpp > 100, 100, dpp))
    return Int(dpp)


def _load_or_compute_dpp(period: str, start_year: int, end_year: int, recompute_if_missing: bool = True) -> Tuple[Raster, str]:
    """
    Returns (dpp_raster, source_tag)
    """
    change_path = os.path.join(cfg.NLCD_TCC_CHANGE_DIR, f"nlcd_tcc_change_{period}.tif")
    if _exists(change_path):
        return Raster(change_path), "existing_change_tif"

    if not recompute_if_missing:
        raise FileNotFoundError(f"Missing pp-change raster for {period}: {change_path}")

    logging.warning("pp-change raster missing for %s; recomputing from TCC endpoints.", period)
    return _compute_dpp_from_tcc(period, start_year, end_year), "recomputed_from_tcc"


def _loss_from_dpp(dpp: Raster) -> Raster:
    return Int(Con(dpp < 0, Abs(dpp), 0))


def _build_mask(mask_path: str, mask0_outside: bool) -> Raster | str:
    if not mask_path:
        return mask_path

    desc = arcpy.Describe(mask_path)
    if getattr(desc, "dataType", "").lower() in ("featureclass", "featurelayer", "shapefile"):
        return mask_path

    if not mask0_outside:
        return mask_path

    m = Raster(mask_path)
    return SetNull(m == 0, 1)


def _forest_binary(lc_raster: Raster) -> Raster:
    classes = list(getattr(cfg, "NLCD_FOREST_CLASSES", [41, 42, 43]))
    if not classes:
        raise ValueError("cfg.NLCD_FOREST_CLASSES is empty; cannot build AOI mask.")
    cond = (lc_raster == int(classes[0]))
    for cls in classes[1:]:
        cond = cond | (lc_raster == int(cls))
    return Con(cond, 1)


def _period_aoi_path(period: str) -> str:
    mode = getattr(cfg, "AOI_MASK_BUILD_MODE", "forest_remains_forest")
    out_dir = getattr(cfg, "NLCD_AOI_MASK_DIR", os.path.join(cfg.NLCD_HARVEST_ROOT, "AOI_masks"))
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"aoi_{mode}_{period}.tif")


def _build_period_aoi_mask(period: str, force: bool = False) -> str:
    out_path = _period_aoi_path(period)
    if _exists(out_path) and not force:
        return out_path

    sy, ey = _parse_period(period)
    lc_start = cfg.NLCD_LC_RASTERS.get(sy)
    lc_end   = cfg.NLCD_LC_RASTERS.get(ey)
    if not (_exists(lc_start) and _exists(lc_end)):
        raise FileNotFoundError(
            f"Cannot build AOI for {period}: missing NLCD LC endpoint(s). start={lc_start}, end={lc_end}"
        )

    start_forest = _forest_binary(Raster(lc_start))
    end_forest   = _forest_binary(Raster(lc_end))
    aoi = SetNull((start_forest != 1) | (end_forest != 1), 1)
    aoi.save(out_path)
    return out_path


def _resolve_period_mask(period: str, args: argparse.Namespace):
    if args.mask:
        return _build_mask(args.mask, args.mask0_outside), "manual", args.mask

    if not args.auto_aoi:
        return None, "none", ""

    try:
        path = _build_period_aoi_mask(period, force=args.aoi_force_rebuild)
        return path, "auto_forest_remaining_forest", path
    except Exception:
        if args.strict_aoi:
            raise
        logging.warning("Failed to build auto AOI for %s; proceeding without mask.", period, exc_info=True)
        return None, "none", ""


# ---------- TabulateArea-based distribution (exact loss values) ----------

def _field_suffix_int(name: str) -> Optional[int]:
    m = re.match(r"^VALUE_(\-?\d+)$", name.upper())
    return int(m.group(1)) if m else None


def _tabulate_area_zone_loss(
    zone_path: str,
    loss_int: Raster,
    out_table: str,
    *,
    mask_obj=None
) -> str:
    """
    Runs TabulateArea for zone (harvest class) vs loss_int (0..100).
    """
    zone = Raster(zone_path)

    # Keep only 1..4 zones; drop 0/other to NoData so table is smaller and focused.
    zone_h = SetNull((zone < 1) | (zone > 4), zone)

    # Ensure loss is NoData wherever zone is NoData (prevents counting outside harvest zones)
    loss_h = SetNull(IsNull(zone_h), loss_int)

    if mask_obj:
        with arcpy.EnvManager(mask=mask_obj):
            TabulateArea(zone_h, "Value", loss_h, "Value", out_table)
    else:
        TabulateArea(zone_h, "Value", loss_h, "Value", out_table)

    return out_table


def _read_tabulate_area_counts(out_table: str, cell_area_map2: float) -> Dict[int, Dict[int, int]]:
    """
    Reads TabulateArea output table and converts areas (map units^2) to cell counts.

    Returns:
      {zone_value: {loss_value: cell_count}}
    """
    fields = arcpy.ListFields(out_table)

    zone_field = next((f.name for f in fields if f.name.lower() == "value"), None)
    if not zone_field:
        raise RuntimeError(f"TabulateArea table missing zone field 'Value'. Fields: {[f.name for f in fields]}")

    hist_fields: List[Tuple[int, str]] = []
    for f in fields:
        suf = _field_suffix_int(f.name)
        if suf is not None:
            hist_fields.append((suf, f.name))
    hist_fields.sort(key=lambda x: x[0])

    if not hist_fields:
        raise RuntimeError(f"TabulateArea table has no VALUE_<n> fields. Fields: {[f.name for f in fields]}")

    # Log basic field info
    loss_min = hist_fields[0][0]
    loss_max = hist_fields[-1][0]
    logging.info("TabulateArea fields: %d loss classes (VALUE_%d..VALUE_%d)", len(hist_fields), loss_min, loss_max)

    zone_hists: Dict[int, Dict[int, int]] = {}
    cursor_fields = [zone_field] + [nm for _, nm in hist_fields]

    with arcpy.da.SearchCursor(out_table, cursor_fields) as cur:
        for row in cur:
            z_raw = row[0]
            if z_raw is None:
                continue
            z = int(z_raw)
            if z not in (1, 2, 3, 4):
                continue
            hist = zone_hists.setdefault(z, {})
            for (loss_val, _fname), area_val in zip(hist_fields, row[1:]):
                if area_val is None:
                    continue
                a = float(area_val)
                if a <= 0:
                    continue
                # Convert area to cell count (area should be multiple of cell_area_map2)
                cnt = int(round(a / cell_area_map2))
                if cnt > 0:
                    hist[loss_val] = hist.get(loss_val, 0) + cnt

    return zone_hists


def _weighted_median_from_hist(hist: Dict[int, int]) -> Optional[int]:
    total = sum(hist.values())
    if total <= 0:
        return None
    target = (total + 1) / 2
    cum = 0
    for v in sorted(hist):
        cum += hist[v]
        if cum >= target:
            return v
    return max(hist) if hist else None


def _cum_pct_le(hist: Dict[int, int], k: int) -> float:
    total = sum(hist.values())
    if total <= 0:
        return 0.0
    le = sum(c for v, c in hist.items() if v <= k)
    return 100.0 * le / total


def _sum_counts_in_range(hist: Dict[int, int], lo: int, hi: int) -> int:
    return sum(c for v, c in hist.items() if lo <= v <= hi)


def _top_n(hist: Dict[int, int], n: int = 8) -> List[Tuple[int, int]]:
    return sorted(hist.items(), key=lambda kv: kv[1], reverse=True)[:n]


# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser(description="QA harvest low-severity distribution (Option A support).")
    ap.add_argument("--periods", default="latest4")
    ap.add_argument("--workflow", default=getattr(cfg, "HARVEST_WORKFLOW", "nlcd_tcc_severity"))
    ap.add_argument("--mask", default="")
    ap.add_argument("--mask0-outside", action="store_true")
    ap.add_argument("--auto-aoi", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--aoi-force-rebuild", action="store_true")
    ap.add_argument("--strict-aoi", action="store_true")
    ap.add_argument("--prefer-harvest-counted", action="store_true")
    ap.add_argument("--no-recompute-change", action="store_true")

    # New logging controls (optional)
    ap.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR (default INFO)")
    ap.add_argument("--log-file", default="", help="Optional path to write a run log file.")
    ap.add_argument("--keep-temp", action="store_true", help="Keep TabulateArea tables in scratchGDB (debug).")

    args = ap.parse_args()
    _setup_logging(args.log_level, args.log_file)

    logging.info("Starting QA distribution script (workflow=%s)", args.workflow)

    if not _exists(cfg.NLCD_RASTER):
        raise FileNotFoundError(f"Reference raster missing: {cfg.NLCD_RASTER}")

    arcpy.CheckOutExtension("Spatial")
    _set_env_from_reference(cfg.NLCD_RASTER)

    cell_area_map2, cell_ha = _cell_area_map2_and_ha_from_reference(cfg.NLCD_RASTER)
    logging.info("Cell area: %.6f ha (%.6f map_units^2)", cell_ha, cell_area_map2)

    periods_available = list(getattr(cfg, "TIME_PERIODS_TCC", cfg.TIME_PERIODS).keys())

    def _sort_key(p: str):
        a, b = _parse_period(p)
        return (b, a)

    periods_available.sort(key=_sort_key)

    if args.periods.lower() == "all":
        periods = periods_available
    elif args.periods.lower() == "latest4":
        periods = periods_available[-4:] if len(periods_available) >= 4 else periods_available
    else:
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]

    if not periods:
        raise RuntimeError("No periods selected for analysis.")

    logging.info("Analyzing periods: %s", ", ".join(periods))

    out_dir = os.path.join(cfg.NLCD_HARVEST_ROOT, "diagnostics", "loss_threshold_QA")
    os.makedirs(out_dir, exist_ok=True)

    candidate_min_losses = [2, 3, 4, 5, 7, 10]
    report_bins = [1, 2, 3, 5, 10, 15, 20, 25]

    out_long      = os.path.join(out_dir, "loss_hist_long.csv")
    out_summary   = os.path.join(out_dir, "loss_hist_summary.csv")
    out_impact    = os.path.join(out_dir, "threshold_impact.csv")
    out_aoi_diag  = os.path.join(out_dir, "aoi_diagnostics.csv")
    out_run_diag  = os.path.join(out_dir, "run_diagnostics.csv")

    # CSV headers
    with open(out_long, "w", newline="") as f:
        csv.writer(f).writerow([
            "period", "zone_raster", "aoi_source", "aoi_mask_path",
            "harvest_class", "loss_pp", "cell_count", "area_ha", "pct_within_class"
        ])

    with open(out_summary, "w", newline="") as f:
        csv.writer(f).writerow([
            "period", "zone_raster", "aoi_source", "aoi_mask_path", "harvest_class",
            "total_area_ha", "median_loss_pp",
            *[f"pct_area_le_{k}pp" for k in report_bins]
        ])

    with open(out_impact, "w", newline="") as f:
        csv.writer(f).writerow([
            "period", "zone_raster", "aoi_source", "aoi_mask_path",
            "total_harvest_area_ha",
            "min_loss_pp",
            "removed_area_ha",
            "removed_pct_of_harvest",
            "remaining_area_ha",
            "remaining_pct_of_harvest"
        ])

    with open(out_aoi_diag, "w", newline="") as f:
        csv.writer(f).writerow([
            "period", "aoi_source", "aoi_mask_path",
            "analyzed_harvest_cell_count", "analyzed_harvest_area_ha",
        ])

    with open(out_run_diag, "w", newline="") as f:
        csv.writer(f).writerow([
            "period",
            "zone_label", "zone_path",
            "aoi_source", "aoi_mask_path",
            "dpp_source",
            "zone_min", "zone_max",
            "mask_count_cells",
            "harvest_cells_total", "harvest_area_ha_total",
            "class1_cells", "class1_area_ha",
            "class1_median_loss_pp",
            "class1_top_loss_vals"
        ])

    try:
        for period in periods:
            t0 = time.perf_counter()
            sy, ey = _parse_period(period)
            logging.info("---- %s ----", period)

            zone_path, zone_label = _resolve_zone_raster(
                period=period,
                workflow=args.workflow,
                prefer_harvest_counted=args.prefer_harvest_counted
            )
            logging.info("Zones: %s (%s)", zone_path, zone_label)

            period_mask_obj, aoi_source, aoi_mask_path = _resolve_period_mask(period, args)
            logging.info("AOI mask for %s: %s (%s)", period, aoi_source, aoi_mask_path or "None")

            # Cheap sanity checks
            zone_min = _safe_raster_prop(zone_path, "MINIMUM")
            zone_max = _safe_raster_prop(zone_path, "MAXIMUM")
            logging.info("Zone raster min/max: %s / %s", zone_min, zone_max)

            mask_count = None
            if period_mask_obj and isinstance(period_mask_obj, str) and _exists(period_mask_obj):
                mask_count = _safe_raster_prop(period_mask_obj, "COUNT")
                logging.info("AOI raster COUNT (non-NoData cells): %s", None if mask_count is None else int(mask_count))

            dpp, dpp_source = _load_or_compute_dpp(
                period=period,
                start_year=sy,
                end_year=ey,
                recompute_if_missing=not args.no_recompute_change
            )
            logging.info("dpp source: %s", dpp_source)

            loss = _loss_from_dpp(dpp)

            # TabulateArea (exact values)
            tmp_table = os.path.join(arcpy.env.scratchGDB, f"ta_{period}_{int(time.time())}")
            try:
                _tabulate_area_zone_loss(zone_path, loss, tmp_table, mask_obj=period_mask_obj)
                zone_hists = _read_tabulate_area_counts(tmp_table, cell_area_map2)
            finally:
                if (not args.keep_temp) and arcpy.Exists(tmp_table):
                    try:
                        arcpy.management.Delete(tmp_table)
                    except Exception:
                        pass

            # Total harvest area (zones 1..4)
            total_harvest_cells = sum(sum(zone_hists.get(z, {}).values()) for z in (1, 2, 3, 4))
            total_harvest_area_ha = total_harvest_cells * cell_ha

            logging.info("Total harvest (zones 1..4): %.0f ha (%d cells)", total_harvest_area_ha, total_harvest_cells)

            # AOI diagnostics (harvest-only intersection)
            with open(out_aoi_diag, "a", newline="") as f:
                csv.writer(f).writerow([
                    period, aoi_source, aoi_mask_path,
                    total_harvest_cells, f"{total_harvest_area_ha:.6f}"
                ])

            # If harvest is unexpectedly tiny/zero, emit extra diagnostics
            if total_harvest_cells == 0:
                logging.error(
                    "No harvest cells found for %s. This usually means: "
                    "(a) AOI mask excludes everything, (b) zone raster has no 1..4 inside AOI, "
                    "or (c) rasters misaligned/resampled unexpectedly.", period
                )
                logging.error("Quick checks: zone min/max=%s/%s, AOI COUNT=%s", zone_min, zone_max, mask_count)

            # Write per-zone long + summary
            for z in sorted(zone_hists.keys()):
                if z not in (1, 2, 3, 4):
                    continue
                hist = zone_hists.get(z, {})
                total_cells = sum(hist.values())
                if total_cells <= 0:
                    continue

                total_area_ha = total_cells * cell_ha
                med = _weighted_median_from_hist(hist)

                with open(out_long, "a", newline="") as f:
                    w = csv.writer(f)
                    for loss_pp in sorted(hist):
                        c = hist[loss_pp]
                        area_ha = c * cell_ha
                        pct = 100.0 * c / total_cells
                        w.writerow([period, zone_label, aoi_source, aoi_mask_path, z, loss_pp, c, f"{area_ha:.6f}", f"{pct:.4f}"])

                with open(out_summary, "a", newline="") as f:
                    w = csv.writer(f)
                    row = [
                        period, zone_label, aoi_source, aoi_mask_path, z,
                        f"{total_area_ha:.6f}",
                        "" if med is None else med,
                    ]
                    row += [f"{_cum_pct_le(hist, k):.4f}" for k in report_bins]
                    w.writerow(row)

                # Helpful console logging for class 1
                if z == 1:
                    pct_le_5 = _cum_pct_le(hist, 5)
                    pct_le_10 = _cum_pct_le(hist, 10)
                    top = _top_n(hist, 6)
                    logging.info(
                        "Class 1 (%s): area=%.0f ha, median=%s pp, pct<=5pp=%.2f%%, pct<=10pp=%.2f%%, top=%s",
                        period, total_area_ha, str(med), pct_le_5, pct_le_10, top
                    )

            # Threshold impacts (removing low-loss portion of class 1)
            class1_hist = zone_hists.get(1, {})
            for tmin in candidate_min_losses:
                removed_cells = _sum_counts_in_range(class1_hist, 1, tmin - 1) if tmin > 1 else 0
                removed_area_ha = removed_cells * cell_ha
                remaining_area_ha = max(total_harvest_area_ha - removed_area_ha, 0.0)

                removed_pct = (100.0 * removed_area_ha / total_harvest_area_ha) if total_harvest_area_ha > 0 else 0.0
                remaining_pct = 100.0 - removed_pct if total_harvest_area_ha > 0 else 0.0

                with open(out_impact, "a", newline="") as f:
                    csv.writer(f).writerow([
                        period, zone_label, aoi_source, aoi_mask_path,
                        f"{total_harvest_area_ha:.6f}",
                        tmin,
                        f"{removed_area_ha:.6f}",
                        f"{removed_pct:.4f}",
                        f"{remaining_area_ha:.6f}",
                        f"{remaining_pct:.4f}",
                    ])

            # Run diagnostics row
            class1_cells = sum(class1_hist.values())
            class1_area = class1_cells * cell_ha
            class1_med = _weighted_median_from_hist(class1_hist) if class1_cells > 0 else None
            class1_top = _top_n(class1_hist, 8) if class1_cells > 0 else []
            with open(out_run_diag, "a", newline="") as f:
                csv.writer(f).writerow([
                    period,
                    zone_label, zone_path,
                    aoi_source, aoi_mask_path,
                    dpp_source,
                    zone_min, zone_max,
                    "" if mask_count is None else int(mask_count),
                    total_harvest_cells, f"{total_harvest_area_ha:.6f}",
                    class1_cells, f"{class1_area:.6f}",
                    "" if class1_med is None else class1_med,
                    str(class1_top),
                ])

            logging.info("Finished %s in %.1f min", period, (time.perf_counter() - t0) / 60.0)

    finally:
        arcpy.CheckInExtension("Spatial")

    logging.info("Done. Outputs written to: %s", out_dir)
    logging.info("  %s", out_long)
    logging.info("  %s", out_summary)
    logging.info("  %s", out_impact)
    logging.info("  %s", out_aoi_diag)
    logging.info("  %s", out_run_diag)


if __name__ == "__main__":
    main()