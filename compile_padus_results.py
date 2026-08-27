"""
Compile the 8 combined_results.csv files produced by run_padus_forests.py
(2 modes x 4 inventory periods) into a single tagged CSV.

Also joins AOI attributes (mang_name, unit_nm, gap_sts, state_nm, acres_nat)
from the cleaned PAD-US shapefile so downstream rollups by manager/unit/GAP
are a groupby away.

Output:
    national_runs/combined_results_PADUS_UNIT_ALL.csv
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs")

# Each deliverable has its own AOI, run label, compiled CSV and summary folder, so
# runs never overwrite each other. Select with the LEARN_AOI environment variable
# (run_padus_forests.py takes a matching --aoi flag):
#     set LEARN_AOI=nwfp        (Windows)   /   export LEARN_AOI=nwfp   (bash)
AOI_PRESETS = {
    "roadless": {
        "aoi_shp":      Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_roadless.shp"),
        "run_label":    "PADUS_FS_BLM_ROADLESS",
        "combined_csv": "combined_results_PADUS_ROADLESS_ALL.csv",
        "summary_dir":  "summaries_FRF",
    },
    "nwfp": {
        "aoi_shp":      Path(r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp.shp"),
        "run_label":    "PADUS_FS_BLM_NWFP",
        "combined_csv": "combined_results_PADUS_NWFP_ALL.csv",
        "summary_dir":  "summaries_FRF_NWFP",
    },
    # GAP-corrected AOIs. The "roadless" and "nwfp" AOIs above were built from
    # PADUS4_1Fee only, which is 100% GAP 3 for USFS+BLM CONUS, so they carry no
    # protection signal at all -- all designated wilderness reads as "not protected".
    # These rebuild it from PADUS4_1Designation (see prepare_padus_gap_aoi.py) and
    # supersede the two above for anything reporting protection status.
    "gap": {
        "aoi_shp":      Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_gap_roadless.shp"),
        "run_label":    "PADUS_FS_BLM_GAP",
        "combined_csv": "combined_results_PADUS_GAP_ALL.csv",
        "summary_dir":  "summaries_FRF_GAP",
    },
    "nwfp_gap": {
        "aoi_shp":      Path(r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp_gap.shp"),
        "run_label":    "PADUS_FS_BLM_NWFP_GAP",
        "combined_csv": "combined_results_PADUS_NWFP_GAP_ALL.csv",
        "summary_dir":  "summaries_FRF_NWFP_GAP",
    },
}
AOI_PRESET = os.environ.get("LEARN_AOI", "roadless")
if AOI_PRESET not in AOI_PRESETS:
    raise SystemExit(f"LEARN_AOI={AOI_PRESET!r} is not one of {sorted(AOI_PRESETS)}")
_P = AOI_PRESETS[AOI_PRESET]

AOI_DBF = _P["aoi_shp"].with_suffix(".dbf")

RECAT_MODES = ["recat_true", "recat_false"]
INVENTORY_PERIODS = ["2013_2016", "2016_2019", "2019_2021", "2021_2023"]
RUN_LABEL = _P["run_label"]
RUN_SUFFIX = "_BatchProcessing"
CSV_NAME = "combined_results.csv"
FINAL_OUTPUT = BASE_DIR / _P["combined_csv"]
SUMMARY_DIR = BASE_DIR / _P["summary_dir"]


def _parse_folder_date(folder_name: str):
    try:
        return datetime.strptime(folder_name[:10], "%Y_%m_%d")
    except ValueError:
        return None


def find_run_csv(recat_mode: str, inventory_period: str):
    mode_dir = BASE_DIR / recat_mode
    if not mode_dir.exists():
        return None, []
    pattern = f"*_{inventory_period}_{RUN_LABEL}{RUN_SUFFIX}"
    candidates = [p / CSV_NAME for p in mode_dir.glob(pattern) if (p / CSV_NAME).exists()]
    if not candidates:
        return None, []
    if len(candidates) == 1:
        return candidates[0], candidates
    # Pick the newest by folder date, fallback to mtime.
    def key(p):
        d = _parse_folder_date(p.parent.name)
        return (1, d) if d else (0, datetime.fromtimestamp(p.parent.stat().st_mtime))
    return max(candidates, key=key), candidates


def load_aoi_attrs():
    """Pull AOI attributes from the shapefile's DBF using pyogrio (geometry-free read)."""
    try:
        import pyogrio
    except ImportError:
        print(f"WARN: pyogrio not available; skipping AOI attribute join.")
        return None
    shp = AOI_DBF.with_suffix(".shp")
    if not shp.exists():
        print(f"WARN: AOI shapefile not found at {shp}; skipping AOI attribute join.")
        return None
    gdf = pyogrio.read_dataframe(shp, read_geometry=False)
    keep = [c for c in ["unit_id", "mang_name", "unit_nm", "gap_sts", "state_nm", "acres_nat", "roadless"] if c in gdf.columns]
    return gdf[keep].copy()


def main():
    dfs = []
    missing = []

    for recat_mode in RECAT_MODES:
        for inv in INVENTORY_PERIODS:
            csv_path, _ = find_run_csv(recat_mode, inv)
            if csv_path is None:
                missing.append((recat_mode, inv))
                continue
            df = pd.read_csv(csv_path, low_memory=False)
            df.insert(0, "recat_mode", recat_mode)
            df.insert(1, "inventory_period", inv)
            dfs.append(df)

    if not dfs:
        raise SystemExit(
            f"No combined_results.csv found under {BASE_DIR}. "
            f"Check that run_padus_forests.py has produced at least one run."
        )

    combined = pd.concat(dfs, ignore_index=True, sort=False)

    # Join AOI attributes on unit_id (= Geography_ID produced by forests_analysis).
    aoi = load_aoi_attrs()
    if aoi is not None and "Geography_ID" in combined.columns:
        combined = combined.merge(
            aoi, how="left", left_on="Geography_ID", right_on="unit_id"
        )
        if "unit_id" in combined.columns:
            combined = combined.drop(columns=["unit_id"])

    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(FINAL_OUTPUT, index=False)

    print(f"Wrote {FINAL_OUTPUT}")
    print(f"Rows: {len(combined):,}  Columns: {len(combined.columns)}")
    print(f"Run coverage: {len(dfs)}/{len(RECAT_MODES) * len(INVENTORY_PERIODS)}")
    if missing:
        print("Missing combinations:")
        for m, p in missing:
            print(f"  - {m} / {p}")


if __name__ == "__main__":
    main()
