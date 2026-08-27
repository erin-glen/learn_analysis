"""
Preflight checks for a LEARN AOI batch, run before any multi-hour work starts.

Exists because of the 2026-06-04 roadless run: the ArcGIS Pro named-user token had
dropped during a previous ~20 hr batch, so a fresh `import arcpy` process could not
re-checkout a license and the whole recat_true batch died instantly on
"RuntimeError: The Product License has not been initialized" -- while the wrapper
still exited 0. Checking the license and every input path up front turns that class
of failure into an immediate, loud abort.

Exits non-zero if anything is missing, so a wrapper can abort on it.

Run with the ArcGIS Pro python; the AOI comes from compile_padus_results.AOI_PRESETS:
    python preflight_aoi.py            # preset from $LEARN_AOI, else "roadless"
    python preflight_aoi.py nwfp_gap
"""

from __future__ import annotations

import os
import sys

import arcpy

import compile_padus_results as compile_mod
from config import get_input_config

ID_FIELD = "unit_id"
PERIODS = [(2013, 2016), (2016, 2019), (2019, 2021), (2021, 2023)]

problems: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


def main() -> int:
    preset = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LEARN_AOI", "roadless")).lower()
    if preset not in compile_mod.AOI_PRESETS:
        sys.exit(f"unknown preset {preset!r}; use one of {sorted(compile_mod.AOI_PRESETS)}")
    aoi = str(compile_mod.AOI_PRESETS[preset]["aoi_shp"])
    print(f"preset: {preset}   label: {compile_mod.AOI_PRESETS[preset]['run_label']}")
    print(f"AOI:    {aoi}\n")

    print("=== licenses ===")
    arcinfo = arcpy.CheckProduct("ArcInfo")
    check("ArcInfo license", arcinfo == "Available", arcinfo)
    sa = arcpy.CheckExtension("Spatial")
    check("Spatial Analyst extension", sa == "Available", sa)

    print("\n=== AOI ===")
    if not arcpy.Exists(aoi):
        check(f"AOI exists: {aoi}", False)
        return report()
    check(f"AOI exists: {os.path.basename(aoi)}", True)

    fields = [f.name for f in arcpy.ListFields(aoi)]
    check(f"{ID_FIELD} field present", ID_FIELD in fields, ", ".join(fields))

    n = int(arcpy.management.GetCount(aoi)[0])
    check("feature count > 0", n > 0, f"{n:,} features")

    if ID_FIELD in fields:
        with arcpy.da.SearchCursor(aoi, [ID_FIELD]) as cur:
            ids = [r[0] for r in cur]
        check(f"{ID_FIELD} unique", len(set(ids)) == len(ids),
              f"{len(set(ids)):,} distinct of {len(ids):,}")
        check(f"{ID_FIELD} non-null", all(i not in (None, "") for i in ids))

    print("\n=== input rasters (per period) ===")
    for y1, y2 in PERIODS:
        cfg = get_input_config(str(y1), str(y2))
        paths = [cfg["nlcd_1"], cfg["nlcd_2"], cfg["forest_age_raster"],
                 cfg["carbon_ag_bg_us"], cfg["carbon_sd_dd_lt"], cfg["carbon_so"],
                 cfg["forest_lookup_csv"]] + list(cfg["disturbance_rasters"])
        missing = [p for p in paths if not os.path.exists(p)]
        check(f"{y1}-{y2}: {len(paths)} inputs "
              f"({len(cfg['disturbance_rasters'])} disturbance rasters)",
              not missing, "missing: " + "; ".join(missing) if missing else "")

    return report()


def report() -> int:
    print()
    if problems:
        print(f"PREFLIGHT FAILED - {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
