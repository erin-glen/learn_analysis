"""
Preflight checks for the NWFP FRF batch, run before any multi-hour work starts.

Exists because of the 2026-06-04 roadless run: the ArcGIS Pro named-user token had
dropped during a previous ~20 hr batch, so a fresh `import arcpy` process could not
re-checkout a license and the whole recat_true batch died instantly on
"RuntimeError: The Product License has not been initialized" — while the wrapper
still exited 0. Checking the license and every input path up front turns that class
of failure into an immediate, loud abort.

Exits non-zero if anything is missing, so a wrapper can abort on it.

Run with the ArcGIS Pro python:
    "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" preflight_nwfp.py
"""

from __future__ import annotations

import os
import sys

import arcpy

from config import get_input_config

AOI = r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp.shp"
ID_FIELD = "unit_id"
EXPECTED_FEATURES = 94
PERIODS = [(2013, 2016), (2016, 2019), (2019, 2021), (2021, 2023)]

problems: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


def main() -> int:
    print("=== licenses ===")
    arcinfo = arcpy.CheckProduct("ArcInfo")
    check("ArcInfo license", arcinfo == "Available", arcinfo)
    sa = arcpy.CheckExtension("Spatial")
    check("Spatial Analyst extension", sa == "Available", sa)

    print("\n=== AOI ===")
    if not arcpy.Exists(AOI):
        check(f"AOI exists: {AOI}", False)
        return report()
    check(f"AOI exists: {os.path.basename(AOI)}", True)

    fields = [f.name for f in arcpy.ListFields(AOI)]
    check(f"{ID_FIELD} field present", ID_FIELD in fields, ", ".join(fields))

    n = int(arcpy.management.GetCount(AOI)[0])
    check(f"feature count == {EXPECTED_FEATURES}", n == EXPECTED_FEATURES, str(n))

    if ID_FIELD in fields:
        with arcpy.da.SearchCursor(AOI, [ID_FIELD]) as cur:
            ids = [r[0] for r in cur]
        check(f"{ID_FIELD} unique", len(set(ids)) == len(ids),
              f"{len(set(ids))} distinct of {len(ids)}")
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
