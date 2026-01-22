#!/usr/bin/env python
"""
Hardcoded validator for inputs referenced by config.get_input_config.

- Loads your config.py (from same folder by default, or a specific path).
- Validates existence (and optional readability) of all required inputs.
- Supports running for a single (year1, year2) OR all adjacent pairs in VALID_YEARS.
- Prints a summary and optionally writes a CSV report to OUTPUT_BASE_DIR.

Edit the "USER RUN SETTINGS" section below to control execution.
"""

import csv
import os
import sys
import importlib.util
from typing import Dict, List, Optional, Tuple

# =========================================================
# =============== USER RUN SETTINGS (EDIT) ================
# =========================================================

# If your config.py sits next to this script, leave as None.
# Otherwise, set an absolute path like r"C:\GIS\LEARN\config.py"
CONFIG_PATH: Optional[str] = r"C:\git\learn_analysis\config_local.py"

# Choose one mode:
RUN_ALL_PAIRS: bool = True            # True -> run across adjacent pairs in VALID_YEARS
SINGLE_PAIR: Tuple[int, int] = (2016, 2023)  # Used when RUN_ALL_PAIRS == False

# Optional AOI (without .shp). Leave None if not needed.
AOI: Optional[str] = None             # e.g., "Montgomery"

# Tree canopy source to validate: one of None, "NLCD", "CBW", "Local"
TREE_CANOPY_SOURCE: Optional[str] = "NLCD"

# Attempt to open rasters/vectors (requires rasterio and/or fiona/geopandas)
CHECK_READ: bool = False

# Write a CSV report summarizing all checks
WRITE_REPORT: bool = True
REPORT_FILENAME: str = "input_validation_report.csv"  # saved to OUTPUT_BASE_DIR if present

# =========================================================
# ================== INTERNAL FUNCTIONS ===================
# =========================================================

def load_config_module(config_path: Optional[str]):
    """Load user's config.py as a module."""
    if config_path is None:
        try:
            import config as cfg  # type: ignore
            return cfg
        except Exception as e:
            raise ImportError(
                "Could not import 'config' from the current working directory. "
                "Either place this script next to config.py or set CONFIG_PATH."
            ) from e
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    spec = importlib.util.spec_from_file_location("user_config", config_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    return mod

def try_open_raster(path: str) -> Tuple[bool, str]:
    """Try to open a raster with rasterio if available."""
    try:
        import rasterio  # type: ignore
    except Exception:
        return True, "existence-only (rasterio not installed)"
    try:
        with rasterio.open(path) as ds:
            _ = (ds.count, ds.width, ds.height, ds.crs)
        return True, "ok (rasterio open)"
    except Exception as e:
        return False, f"cannot open raster: {e}"

def try_open_vector(path: str) -> Tuple[bool, str]:
    """Try to open a vector file with fiona/geopandas if available."""
    try:
        import fiona  # type: ignore
        try:
            with fiona.open(path, "r") as _:
                pass
            return True, "ok (fiona open)"
        except Exception as e:
            return False, f"cannot open vector (fiona): {e}"
    except Exception:
        try:
            import geopandas as gpd  # type: ignore
            try:
                _ = gpd.read_file(path, rows=1)
                return True, "ok (geopandas read_file)"
            except Exception as e:
                return False, f"cannot open vector (geopandas): {e}"
        except Exception:
            return True, "existence-only (no fiona/geopandas)"

def check_file_exists(path: str) -> Tuple[bool, str]:
    """Existence + non-zero size check."""
    if path is None:
        return False, "path is None"
    if isinstance(path, str) and path.strip().lower() == "none":
        return False, "path is 'None' string"
    if not os.path.exists(path):
        return False, "missing"
    try:
        size = os.path.getsize(path)
    except Exception:
        size = -1
    if size == 0:
        return False, "zero-byte file"
    return True, "exists"

def validate_config_paths(input_config: Dict, check_read: bool) -> List[Tuple[str, str, str]]:
    """
    Validate presence (and optional readability) of all relevant config entries.
    Returns a list of (key, status, note).
    """
    results: List[Tuple[str, str, str]] = []

    # Always-present or expected keys
    keys_to_check = [
        "nlcd_1", "nlcd_2",
        "forest_age_raster",
        "carbon_ag_bg_us", "carbon_sd_dd_lt", "carbon_so",
        "forest_lookup_csv",
    ]

    # Optional keys
    if "aoi" in input_config:
        keys_to_check.append("aoi")
    if "tree_canopy_1" in input_config and "tree_canopy_2" in input_config:
        keys_to_check.extend(["tree_canopy_1", "tree_canopy_2"])

    for key in keys_to_check:
        path = input_config.get(key)
        ok, msg = check_file_exists(path)
        results.append((key, "PASS" if ok else "FAIL", msg))

        # Optional deeper read checks
        if ok and check_read:
            ext = os.path.splitext(str(path))[1].lower()
            if ext in (".tif", ".tiff"):
                rok, rmsg = try_open_raster(path)
                results.append((f"{key}::open", "PASS" if rok else "FAIL", rmsg))
            elif ext == ".shp":
                vok, vmsg = try_open_vector(path)
                results.append((f"{key}::open", "PASS" if vok else "FAIL", vmsg))

    # Disturbance rasters (list)
    dr_list = input_config.get("disturbance_rasters", [])
    if isinstance(dr_list, list):
        if len(dr_list) == 0:
            results.append(("disturbance_rasters", "WARN", "no rasters selected for this year range"))
        for i, path in enumerate(dr_list):
            ok, msg = check_file_exists(path)
            results.append((f"disturbance_rasters[{i}]", "PASS" if ok else "FAIL", msg))
            if ok and check_read:
                if os.path.splitext(str(path))[1].lower() in (".tif", ".tiff"):
                    rok, rmsg = try_open_raster(path)
                    results.append((f"disturbance_rasters[{i}]::open", "PASS" if rok else "FAIL", rmsg))

    return results

def print_results_header():
    print("\n=== Input Data Validation Report ===")
    print(f"{'Key':40} | {'Status':7} | Note")
    print("-" * 90)

def print_results(rows: List[Tuple[str, str, str]]):
    for key, status, note in rows:
        print(f"{key:40} | {status:7} | {note}")

def summarize_status(rows: List[Tuple[str, str, str]]) -> Tuple[int, int, int]:
    fails = sum(1 for _, s, _ in rows if s == "FAIL")
    warns = sum(1 for _, s, _ in rows if s == "WARN")
    passes = sum(1 for _, s, _ in rows if s == "PASS")
    return passes, warns, fails

def validate_one_pair(cfg_mod, year1: int, year2: int,
                      aoi: Optional[str], tcs: Optional[str],
                      check_read: bool) -> Tuple[int, List[Tuple[str, str, str]]]:
    """Validate a single (year1, year2) pair. Returns (exit_code, rows)."""
    # Validate years against VALID_YEARS if present
    valid_years = None
    if hasattr(cfg_mod, "VALID_YEARS"):
        try:
            valid_years = {int(y) for y in getattr(cfg_mod, "VALID_YEARS")}
        except Exception:
            valid_years = None
    if valid_years:
        if year1 not in valid_years or year2 not in valid_years:
            print(f"ERROR: years must be in config.VALID_YEARS: got ({year1}, {year2}), allowed: {sorted(valid_years)}")
            return 1, []

    if year1 > year2:
        print(f"ERROR: year1 must be <= year2 (got {year1}, {year2})")
        return 1, []

    try:
        input_cfg = cfg_mod.get_input_config(str(year1), str(year2), aoi_name=aoi, tree_canopy_source=tcs)
    except Exception as e:
        print(f"ERROR: get_input_config failed for ({year1}, {year2}, aoi={aoi}, tcs={tcs}): {e}")
        return 1, []

    print_results_header()
    print(f"Parameters: years=({year1},{year2}), aoi={aoi}, tree_canopy_source={tcs}")
    rows = validate_config_paths(input_cfg, check_read=check_read)
    print_results(rows)
    passes, warns, fails = summarize_status(rows)
    print("-" * 90)
    print(f"Summary: PASS={passes}  WARN={warns}  FAIL={fails}\n")
    return (0 if fails == 0 else 1), rows

def resolve_report_path(cfg_mod, filename: str) -> str:
    base = getattr(cfg_mod, "OUTPUT_BASE_DIR", os.getcwd())
    return os.path.join(base, filename)

def write_csv_report(rows_with_meta: List[Tuple[int, int, Optional[str], Optional[str], str, str, str]],
                     report_path: str) -> None:
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year1", "year2", "aoi", "tree_canopy_source", "key", "status", "note"])
        for r in rows_with_meta:
            w.writerow(r)
    print(f"Report written: {report_path}")

# =========================================================
# ======================== MAIN ===========================
# =========================================================

def main():
    # Load config module
    try:
        cfg_mod = load_config_module(CONFIG_PATH)
    except Exception as e:
        print(f"ERROR: Could not load config module: {e}")
        sys.exit(1)

    # Run validations
    all_rows_meta: List[Tuple[int, int, Optional[str], Optional[str], str, str, str]] = []
    overall_exit = 0

    if RUN_ALL_PAIRS:
        if not hasattr(cfg_mod, "VALID_YEARS"):
            print("ERROR: RUN_ALL_PAIRS=True requires VALID_YEARS in config.")
            sys.exit(1)
        try:
            vyears = [int(y) for y in getattr(cfg_mod, "VALID_YEARS")]
        except Exception as e:
            print(f"ERROR: VALID_YEARS in config is not parseable as ints: {e}")
            sys.exit(1)
        vyears_sorted = sorted(vyears)
        pairs = list(zip(vyears_sorted[:-1], vyears_sorted[1:]))
        if not pairs:
            print("ERROR: No adjacent year pairs found in VALID_YEARS.")
            sys.exit(1)

        for y1, y2 in pairs:
            print("\n" + "=" * 90)
            print(f"Validating pair: {y1} -> {y2}")
            print("=" * 90)
            rc, rows = validate_one_pair(cfg_mod, y1, y2, AOI, TREE_CANOPY_SOURCE, CHECK_READ)
            if rc != 0:
                overall_exit = 1
            for key, status, note in rows:
                all_rows_meta.append((y1, y2, AOI, TREE_CANOPY_SOURCE, key, status, note))
    else:
        y1, y2 = SINGLE_PAIR
        print("\n" + "=" * 90)
        print(f"Validating pair (single run): {y1} -> {y2}")
        print("=" * 90)
        rc, rows = validate_one_pair(cfg_mod, y1, y2, AOI, TREE_CANOPY_SOURCE, CHECK_READ)
        overall_exit = rc
        for key, status, note in rows:
            all_rows_meta.append((y1, y2, AOI, TREE_CANOPY_SOURCE, key, status, note))

    # Optional CSV report
    if WRITE_REPORT:
        report_path = resolve_report_path(cfg_mod, REPORT_FILENAME)
        try:
            write_csv_report(all_rows_meta, report_path)
        except Exception as e:
            print(f"WARNING: Failed to write CSV report: {e}")

    # Exit code communicates overall pass/fail
    if overall_exit == 0:
        print("✅ All requested validations completed with no FAIL entries.")
    else:
        print("❌ One or more validations reported FAIL entries.")
    sys.exit(overall_exit)

if __name__ == "__main__":
    main()
