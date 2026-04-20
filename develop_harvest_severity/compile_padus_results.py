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

from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs")
AOI_DBF = Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_cleaned.dbf")

RECAT_MODES = ["recat_true", "recat_false"]
INVENTORY_PERIODS = ["2013_2016", "2016_2019", "2019_2021", "2021_2023"]
RUN_LABEL = "PADUS_FS_BLM_UNIT"
RUN_SUFFIX = "_BatchProcessing"
CSV_NAME = "combined_results.csv"
FINAL_OUTPUT = BASE_DIR / "combined_results_PADUS_UNIT_ALL.csv"


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
    keep = [c for c in ["unit_id", "mang_name", "unit_nm", "gap_sts", "state_nm", "acres_nat"] if c in gdf.columns]
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
