#!/usr/bin/env python3
"""
qa_harvest_loss_distribution.py
------------------------------------------------------------
Purpose
  Investigate the distribution of NLCD TCC loss magnitudes (percentage-point loss)
  underlying the harvest severity outputs, with emphasis on the lowest harvest class.

Why this helps (Option A)
  If class 1 is dominated by tiny losses (e.g., 1–2 pp), that is consistent with
  map-to-map noise and suggests using a minimum-loss threshold (e.g., >= 3 or >= 5 pp)
  before classifying severity.

What this script does (per period)
  1) Loads (or recomputes) pp-change raster: dpp = end - start (clipped to [-100,100]).
  2) Converts to loss magnitude raster (integer): loss_pp = abs(dpp) where dpp < 0 else 0.
  3) Uses a zonal histogram to tabulate loss_pp values within harvest classes (zones).
     Zones default to "harvest_counted_{method_tag}_{period}.tif" if present,
     otherwise it falls back to the raw severity raster (0–4).
  4) Writes:
     - loss_hist_long.csv: tidy records of (period, zone, loss_pp, count, area_ha, pct)
     - loss_hist_summary.csv: summary stats per (period, zone) incl. median and cumulative %
     - threshold_impact.csv: effect of candidate minimum-loss thresholds on total harvest area

Optional masking
  By default, the script auto-builds a period AOI mask from NLCD land cover:
  cells must be forest in both period endpoints ("forest remains forest").
  You can still pass --mask to override with a manual AOI (feature or raster).
  If your mask raster is 0/1 (0 outside), use --mask0-outside to treat 0 as NoData outside.

Run examples
  # Default: latest 4 periods with auto-generated per-period forest-remains-forest AOI masks
  python qa_harvest_loss_distribution.py

  # All available periods with auto AOI forced to rebuild cached masks
  python qa_harvest_loss_distribution.py --periods all --aoi-force-rebuild

  # Disable auto AOI entirely (run unmasked unless --mask is provided)
  python qa_harvest_loss_distribution.py --no-auto-aoi

  # Manual AOI override (feature class)
  python qa_harvest_loss_distribution.py --periods 2013_2016,2016_2019,2019_2021,2021_2023 --mask C:\\GIS\\Data\\Masks\\USFS_BLM_forest.gdb\\aoi

  # Manual AOI override (0/1 raster where 0=outside)
  python qa_harvest_loss_distribution.py --mask C:\\GIS\\Data\\Masks\\USFS_BLM_forest_mask.tif --mask0-outside

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
from arcpy.sa import (
    Raster, Con, Abs, Int, IsNull, SetNull, ZonalHistogram
)

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


def _set_env_from_reference(ref_raster: str) -> None:
    d = arcpy.Describe(ref_raster)
    arcpy.env.snapRaster = ref_raster
    arcpy.env.cellSize = ref_raster
    arcpy.env.extent = d.extent
    arcpy.env.outputCoordinateSystem = d.spatialReference
    arcpy.env.pyramid = "NONE"
    arcpy.env.parallelProcessingFactor = getattr(cfg, "PARALLEL_PROCESSING_FACTOR", "90%")
    arcpy.env.overwriteOutput = True


def _cell_area_ha_from_reference(ref_raster: str) -> float:
    d = arcpy.Describe(ref_raster)
    cw = float(getattr(d, "meanCellWidth", 0.0))
    ch = float(getattr(d, "meanCellHeight", 0.0))
    cell_area = abs(cw * ch)

    sr = getattr(d, "spatialReference", None)
    unit = getattr(sr, "linearUnitName", "Unknown") if sr else "Unknown"
    if unit.lower() not in ("meter", "metre"):
        logging.warning(
            "Reference raster linear units appear to be '%s'. "
            "Area conversion to hectares assumes meters. Verify if results look odd.",
            unit
        )

    return cell_area / 10000.0


def _resolve_zone_raster(period: str, workflow: str, prefer_harvest_counted: bool = True) -> Tuple[str, str]:
    """
    Returns (zone_path, zone_label).
    Prefer harvest_counted (post fire/insect masking) when present because that's
    closest to what LEARN should be counting as harvest.
    """
    hcfg = cfg.harvest_product_config(workflow)
    method_tag = hcfg.get("method_tag", "abs")

    counted = os.path.join(cfg.NLCD_FINAL_HARVEST_ONLY_DIR, f"harvest_counted_{method_tag}_{period}.tif")
    severity = cfg.harvest_raster_path(period, workflow=workflow)  # typically nlcd_tcc_severity_{period}.tif

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
    """
    Recomputes dpp = end - start with the same basic validity logic used in your pipeline.
    This is only used if the precomputed change TIFF is missing.
    """
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

    # Keep integer like your saved change products
    return Int(dpp)


def _load_or_compute_dpp(period: str, start_year: int, end_year: int, recompute_if_missing: bool = True) -> Raster:
    change_path = os.path.join(cfg.NLCD_TCC_CHANGE_DIR, f"nlcd_tcc_change_{period}.tif")
    if _exists(change_path):
        return Raster(change_path)

    if not recompute_if_missing:
        raise FileNotFoundError(f"Missing pp-change raster for {period}: {change_path}")

    logging.warning("pp-change raster missing for %s; recomputing from TCC endpoints.", period)
    return _compute_dpp_from_tcc(period, start_year, end_year)


def _loss_from_dpp(dpp: Raster) -> Raster:
    """
    Integer loss magnitude (0..100) where dpp<0, else 0.
    """
    return Int(Con(dpp < 0, Abs(dpp), 0))


def _build_mask(mask_path: str, mask0_outside: bool) -> Raster | str:
    """
    Returns a mask suitable for arcpy.EnvManager(mask=...).
    - If mask is a feature class, just return the path.
    - If mask is a raster and mask0_outside=True, convert 0 -> NoData so outside is excluded.
    """
    if not mask_path:
        return mask_path

    # If it's a feature class / layer, env.mask can use it directly.
    desc = arcpy.Describe(mask_path)
    if getattr(desc, "dataType", "").lower() in ("featureclass", "featurelayer", "shapefile"):
        return mask_path

    # Otherwise treat as raster
    if not mask0_outside:
        return mask_path

    m = Raster(mask_path)
    # Convert 0 to NoData; keep nonzero as 1
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
    lc_end = cfg.NLCD_LC_RASTERS.get(ey)
    if not (_exists(lc_start) and _exists(lc_end)):
        raise FileNotFoundError(
            f"Cannot build AOI for {period}: missing NLCD LC endpoint(s). start={lc_start}, end={lc_end}"
        )

    start_forest = _forest_binary(Raster(lc_start))
    end_forest = _forest_binary(Raster(lc_end))
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


def _zonal_histogram_table(zone_ras: str, value_ras: Raster, out_table: str) -> str:
    """
    Runs ZonalHistogram and returns out_table.
    """
    # Use "Value" as the zone field for raster zones.
    # ignore_nodata="DATA" avoids counting NoData in the value raster.
    # zones_as_rows="ZONES_AS_ROWS" is required by some ArcGIS Pro versions.
    ZonalHistogram(zone_ras, "Value", value_ras, out_table, "DATA", "ZONES_AS_ROWS")
    return out_table


def _field_suffix_int(name: str) -> Optional[int]:
    """
    For histogram fields like VALUE_17 -> 17
    """
    m = re.match(r"^VALUE_(\-?\d+)$", name.upper())
    return int(m.group(1)) if m else None


def _read_zonal_hist(out_table: str) -> Tuple[str, List[Tuple[int, Dict[int, int]]]]:
    """
    Returns (zone_field_name, list of (zone_value, {loss_value: count})).
    """
    fields = arcpy.ListFields(out_table)
    zone_field = None
    for f in fields:
        if f.name.lower() == "value":  # common for raster zone field
            zone_field = f.name
            break
    if zone_field is None:
        # Fallback: pick the first integer field that is not VALUE_*
        candidates = [
            f.name for f in fields
            if f.type in ("Integer", "SmallInteger") and not f.name.upper().startswith("VALUE_")
        ]
        if not candidates:
            raise RuntimeError(f"Could not identify zone field in {out_table}")
        zone_field = candidates[0]

    hist_fields = []
    for f in fields:
        suf = _field_suffix_int(f.name)
        if suf is not None:
            hist_fields.append((suf, f.name))
    hist_fields.sort(key=lambda x: x[0])

    rows: List[Tuple[int, Dict[int, int]]] = []
    with arcpy.da.SearchCursor(out_table, [zone_field] + [nm for _, nm in hist_fields]) as cur:
        for r in cur:
            z = int(r[0])
            d: Dict[int, int] = {}
            for (loss_val, fname), count in zip(hist_fields, r[1:]):
                if count is None:
                    continue
                c = int(count)
                if c > 0:
                    d[loss_val] = c
            rows.append((z, d))

    return zone_field, rows


def _weighted_median_from_hist(hist: Dict[int, int]) -> Optional[int]:
    """
    hist: {value: count}
    Returns the smallest value where cumulative count >= 50% of total.
    """
    total = sum(hist.values())
    if total <= 0:
        return None
    target = (total + 1) / 2  # median for discrete counts
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


# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser(description="QA harvest low-severity distribution (Option A support).")
    ap.add_argument(
        "--periods",
        default="latest4",
        help=("Periods to analyze. Options: 'latest4' (default), 'all', or comma-separated list like "
              "'2013_2016,2016_2019,2019_2021,2021_2023'.")
    )
    ap.add_argument(
        "--workflow",
        default=getattr(cfg, "HARVEST_WORKFLOW", "nlcd_tcc_severity"),
        help="Harvest workflow key (default: cfg.HARVEST_WORKFLOW). Typically 'nlcd_tcc_severity'."
    )
    ap.add_argument(
        "--mask",
        default="",
        help=("Optional manual AOI mask (feature class or raster). "
              "If supplied, this overrides auto-generated per-period AOI masks.")
    )
    ap.add_argument(
        "--mask0-outside",
        action="store_true",
        help="If manual --mask is a 0/1 raster where 0 means outside, convert 0 to NoData for masking."
    )
    ap.add_argument(
        "--auto-aoi",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable automatic per-period AOI masks (default: enabled)."
    )
    ap.add_argument(
        "--aoi-force-rebuild",
        action="store_true",
        help="Rebuild period AOI masks even when cached outputs already exist."
    )
    ap.add_argument(
        "--strict-aoi",
        action="store_true",
        help="Fail on auto-AOI build errors instead of warning and continuing unmasked."
    )
    ap.add_argument(
        "--prefer-harvest-counted",
        action="store_true",
        help="Prefer harvest_counted_* as zones when available (recommended)."
    )
    ap.add_argument(
        "--no-recompute-change",
        action="store_true",
        help="Fail if nlcd_tcc_change_{period}.tif is missing instead of recomputing from TCC."
    )
    args = ap.parse_args()

    logging.info("Starting QA distribution script (workflow=%s)", args.workflow)

    if not _exists(cfg.NLCD_RASTER):
        raise FileNotFoundError(f"Reference raster missing: {cfg.NLCD_RASTER}")

    arcpy.CheckOutExtension("Spatial")
    _set_env_from_reference(cfg.NLCD_RASTER)
    cell_ha = _cell_area_ha_from_reference(cfg.NLCD_RASTER)
    logging.info("Cell area = %.6f ha", cell_ha)

    # Choose periods
    periods_available = list(getattr(cfg, "TIME_PERIODS_TCC", cfg.TIME_PERIODS).keys())
    # Sort by end year then start year
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

    # Output directory
    out_dir = os.path.join(cfg.NLCD_HARVEST_ROOT, "diagnostics", "loss_threshold_QA")
    os.makedirs(out_dir, exist_ok=True)

    # Candidate minimum-loss thresholds to evaluate (pp)
    candidate_min_losses = [2, 3, 4, 5, 7, 10]
    # Cumulative bins to report as "what fraction of this class is <= k pp"
    report_bins = [1, 2, 3, 5, 10, 15, 20, 25]

    # Output CSV paths
    out_long = os.path.join(out_dir, "loss_hist_long.csv")
    out_summary = os.path.join(out_dir, "loss_hist_summary.csv")
    out_impact = os.path.join(out_dir, "threshold_impact.csv")
    out_aoi_diag = os.path.join(out_dir, "aoi_diagnostics.csv")

    # Write headers
    with open(out_long, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "zone_raster", "aoi_source", "aoi_mask_path", "harvest_class", "loss_pp", "cell_count", "area_ha", "pct_within_class"])

    with open(out_summary, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "period", "zone_raster", "aoi_source", "aoi_mask_path", "harvest_class",
            "total_area_ha", "median_loss_pp",
            *[f"pct_area_le_{k}pp" for k in report_bins]
        ])

    with open(out_impact, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "period", "zone_raster", "aoi_source", "aoi_mask_path",
            "total_harvest_area_ha",
            "min_loss_pp",
            "removed_area_ha",
            "removed_pct_of_harvest",
            "remaining_area_ha",
            "remaining_pct_of_harvest"
        ])

    with open(out_aoi_diag, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "aoi_source", "aoi_mask_path", "analyzed_cell_count", "analyzed_area_ha"])

    # Main loop
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
            if aoi_source == "manual":
                logging.info("AOI mask for %s: manual (%s)", period, aoi_mask_path)
            elif aoi_source.startswith("auto"):
                logging.info("AOI mask for %s: auto (%s)", period, aoi_mask_path)
            else:
                logging.info("AOI mask for %s: none", period)

            dpp = _load_or_compute_dpp(
                period=period,
                start_year=sy,
                end_year=ey,
                recompute_if_missing=not args.no_recompute_change
            )
            loss = _loss_from_dpp(dpp)

            # Run zonal histogram under period-specific optional mask
            tmp_table = os.path.join(arcpy.env.scratchGDB, f"zh_{period}_{int(time.time())}")
            if period_mask_obj:
                with arcpy.EnvManager(mask=period_mask_obj):
                    _zonal_histogram_table(zone_path, loss, tmp_table)
            else:
                _zonal_histogram_table(zone_path, loss, tmp_table)

            _, rows = _read_zonal_hist(tmp_table)
            analyzed_cells = sum(sum(h.values()) for _, h in rows)
            with open(out_aoi_diag, "a", newline="") as f:
                w = csv.writer(f)
                w.writerow([period, aoi_source, aoi_mask_path, analyzed_cells, f"{analyzed_cells * cell_ha:.6f}"])

            # Convert to per-zone hist and write long + summary
            zone_hists: Dict[int, Dict[int, int]] = {z: h for z, h in rows}

            # Total harvest area for impact calc (zones 1..4)
            total_harvest_cells = 0
            for z in (1, 2, 3, 4):
                h = zone_hists.get(z, {})
                total_harvest_cells += sum(h.values())
            total_harvest_area_ha = total_harvest_cells * cell_ha

            # Write long table + summary per zone
            for z in sorted(zone_hists.keys()):
                if z not in (1, 2, 3, 4):
                    continue  # ignore 0 and any other codes
                hist = zone_hists.get(z, {})
                total_cells = sum(hist.values())
                if total_cells <= 0:
                    continue
                total_area_ha = total_cells * cell_ha
                med = _weighted_median_from_hist(hist)

                # Long records
                with open(out_long, "a", newline="") as f:
                    w = csv.writer(f)
                    for loss_pp in sorted(hist):
                        c = hist[loss_pp]
                        area_ha = c * cell_ha
                        pct = 100.0 * c / total_cells
                        w.writerow([period, zone_label, aoi_source, aoi_mask_path, z, loss_pp, c, f"{area_ha:.6f}", f"{pct:.4f}"])

                # Summary record
                with open(out_summary, "a", newline="") as f:
                    w = csv.writer(f)
                    row = [
                        period, zone_label, aoi_source, aoi_mask_path, z,
                        f"{total_area_ha:.6f}",
                        "" if med is None else med,
                    ]
                    row += [f"{_cum_pct_le(hist, k):.4f}" for k in report_bins]
                    w.writerow(row)

                if z == 1:
                    # Quick console log for class 1 signal
                    pct_le_2 = _cum_pct_le(hist, 2)
                    pct_le_5 = _cum_pct_le(hist, 5)
                    logging.info(
                        "Class 1 (%s): area=%.0f ha, median=%s pp, pct<=2pp=%.1f%%, pct<=5pp=%.1f%%",
                        period, total_area_ha, str(med), pct_le_2, pct_le_5
                    )

            # Threshold impacts (removing low-loss portion of class 1)
            class1_hist = zone_hists.get(1, {})
            for tmin in candidate_min_losses:
                # If we require loss >= tmin, we remove losses in [1 .. tmin-1] from class 1.
                removed_cells = _sum_counts_in_range(class1_hist, 1, tmin - 1) if tmin > 1 else 0
                removed_area_ha = removed_cells * cell_ha
                remaining_area_ha = max(total_harvest_area_ha - removed_area_ha, 0.0)

                removed_pct = (100.0 * removed_area_ha / total_harvest_area_ha) if total_harvest_area_ha > 0 else 0.0
                remaining_pct = 100.0 - removed_pct if total_harvest_area_ha > 0 else 0.0

                with open(out_impact, "a", newline="") as f:
                    w = csv.writer(f)
                    w.writerow([
                        period, zone_label, aoi_source, aoi_mask_path,
                        f"{total_harvest_area_ha:.6f}",
                        tmin,
                        f"{removed_area_ha:.6f}",
                        f"{removed_pct:.4f}",
                        f"{remaining_area_ha:.6f}",
                        f"{remaining_pct:.4f}",
                    ])

            logging.info("Finished %s in %.1f min", period, (time.perf_counter() - t0) / 60.0)

    finally:
        arcpy.CheckInExtension("Spatial")

    logging.info("Done. Outputs written to: %s", out_dir)
    logging.info("  %s", out_long)
    logging.info("  %s", out_summary)
    logging.info("  %s", out_impact)
    logging.info("  %s", out_aoi_diag)


if __name__ == "__main__":
    main()
