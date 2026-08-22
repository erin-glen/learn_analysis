"""
Verify (mode x period) coverage for an AOI preset's batch runs.

Standalone because the awk-based `period_done` check inside run_nwfp_pipeline.sh is
wrong: it splits the CSV header on commas, but `"Area (ha, total)"` contains one, so
the Geography_ID column index is off and it always counts 1 distinct id. That makes
the wrapper's closing coverage check report 0/8 and exit 1 even on a fully successful
run. Use this instead; it parses the CSV properly.

    python verify_nwfp_coverage.py            # LEARN_AOI or 'nwfp'
    python verify_nwfp_coverage.py roadless
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

BASE = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs")
PERIODS = ["2013_2016", "2016_2019", "2019_2021", "2021_2023"]
MODES = {"recat_false": "normal", "recat_true": "recategorize"}
LABELS = {"nwfp": "PADUS_FS_BLM_NWFP", "roadless": "PADUS_FS_BLM_ROADLESS"}
EXPECTED = {"nwfp": 94, "roadless": 903}


def newest_csv(mode_dir: Path, period: str, label: str):
    """Return the newest complete combined_results.csv for this (mode, period)."""
    cands = sorted(mode_dir.glob(f"*_{period}_{label}_BatchProcessing"))
    best = (None, -1)
    for d in cands:
        csv = d / "combined_results.csv"
        if not csv.exists():
            continue
        try:
            n = pd.read_csv(csv, low_memory=False)["Geography_ID"].nunique()
        except Exception as e:
            print(f"    ! unreadable {csv.parent.name}: {e}")
            continue
        if n > best[1]:
            best = (csv, n)
    return best


def main() -> int:
    preset = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LEARN_AOI", "nwfp")).lower()
    if preset not in LABELS:
        sys.exit(f"unknown preset {preset!r}; use one of {sorted(LABELS)}")
    label, exp = LABELS[preset], EXPECTED[preset]
    thresh = int(exp * 0.95)

    print(f"preset={preset}  label={label}  expected features={exp} (threshold {thresh})\n")
    done = 0
    for mode_dir_name, mode_flag in MODES.items():
        for period in PERIODS:
            csv, n = newest_csv(BASE / mode_dir_name, period, label)
            if csv is None:
                print(f"  MISSING  {mode_dir_name}/{period}  (no combined_results.csv)")
            elif n >= thresh:
                print(f"  OK       {mode_dir_name}/{period}  {n} features  [{csv.parent.name}]")
                done += 1
            else:
                print(f"  PARTIAL  {mode_dir_name}/{period}  {n} of {exp} features  [{csv.parent.name}]")

    print(f"\ncoverage: {done}/8")
    if done != 8:
        print("INCOMPLETE - do not treat the compiled outputs as a finished deliverable.")
        return 1
    print("COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
