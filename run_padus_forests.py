"""
Driver for running forests_analysis over the cleaned PAD-US FS+BLM unit-level AOI.

Writes output under
    C:\\GIS\\Data\\LEARN\\Disturbances\\NLCD_harvest_severity\\national_runs\\
        recat_{true,false}\\{YYYY_MM_DD}_{y1}_{y2}_PADUS_FS_BLM_UNIT_BatchProcessing\\

Usage (run with the ArcGIS Pro python):
    arcgispro-py3\\python.exe run_padus_forests.py --mode recategorize
    arcgispro-py3\\python.exe run_padus_forests.py --mode normal
    arcgispro-py3\\python.exe run_padus_forests.py --mode both            # sequential
    arcgispro-py3\\python.exe run_padus_forests.py --mode recategorize --periods 2013_2016,2016_2019
    arcgispro-py3\\python.exe run_padus_forests.py --mode normal --test   # first 10 features, one period
"""

import argparse
import os
import sys
from unittest.mock import patch

import forests_analysis

# AOI presets, keyed the same way as compile_padus_results.AOI_PRESETS. Pick one
# with --aoi (default: the LEARN_AOI env var, else "roadless").
AOI_PRESETS = {
    "roadless": (r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_roadless.shp",
                 "PADUS_FS_BLM_ROADLESS"),
    "nwfp":     (r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp.shp",
                 "PADUS_FS_BLM_NWFP"),
    "gap":      (r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_gap_roadless.shp",
                 "PADUS_FS_BLM_GAP"),
    "nwfp_gap": (r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp_gap.shp",
                 "PADUS_FS_BLM_NWFP_GAP"),
}
ID_FIELD = "unit_id"

OUTPUT_ROOT = r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs"
OUTPUT_DIRS = {
    "recategorize": os.path.join(OUTPUT_ROOT, "recat_true"),
    "normal":       os.path.join(OUTPUT_ROOT, "recat_false"),
}

INVENTORY_PERIODS = [
    (2013, 2016),
    (2016, 2019),
    (2019, 2021),
    (2021, 2023),
]


def _period_key(y1, y2):
    return f"{y1}_{y2}"


def run_one(mode_label, year1, year2, aoi_shp, run_label, test=False):
    """mode_label is 'recategorize' or 'normal'."""
    analysis_mode = "recategorize" if mode_label == "recategorize" else ("test" if test else None)
    # Note: forests_analysis treats 'test' and 'recategorize' as mutually exclusive.
    # If both --mode recategorize and --test are set, we still honor recategorize
    # but cap the feature loop by temporarily patching the cursor is out of scope here;
    # use --test only with --mode normal for a dry run of the full pipeline.
    if mode_label == "recategorize" and test:
        print("WARN: --test has no effect when mode=recategorize; running full recat mode.")

    output_base_dir = OUTPUT_DIRS[mode_label]
    os.makedirs(output_base_dir, exist_ok=True)

    print(f"\n=== {mode_label.upper()} | {year1}-{year2} | {run_label} | out={output_base_dir} ===")
    # forests_analysis.main() still prompts for year1/year2 via input(); mock those.
    with patch("builtins.input", side_effect=[str(year1), str(year2)]):
        forests_analysis.main(
            mode=analysis_mode,
            aoi_shapefile=aoi_shp,
            id_field=ID_FIELD,
            run_label=run_label,
            output_base_dir=output_base_dir,
        )


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["recategorize", "normal", "both"], default="both",
                   help="Which analysis mode(s) to run (default: both).")
    p.add_argument("--periods", default=None,
                   help="Comma-separated inventory periods, e.g. '2013_2016,2016_2019'. "
                        "Default: all four.")
    p.add_argument("--test", action="store_true",
                   help="Dry-run: first 10 features only (mode=normal only).")
    p.add_argument("--aoi", choices=sorted(AOI_PRESETS),
                   default=os.environ.get("LEARN_AOI", "roadless"),
                   help="Which AOI preset to run (default: $LEARN_AOI, else 'roadless'). "
                        "Must match LEARN_AOI when compiling/summarizing.")
    return p.parse_args()


def main():
    args = parse_args()

    if args.periods:
        wanted = set(args.periods.split(","))
        periods = [(y1, y2) for (y1, y2) in INVENTORY_PERIODS if _period_key(y1, y2) in wanted]
        if not periods:
            sys.exit(f"No matching periods for --periods={args.periods}")
    else:
        periods = INVENTORY_PERIODS

    modes = ["recategorize", "normal"] if args.mode == "both" else [args.mode]

    aoi_shp, run_label = AOI_PRESETS[args.aoi]
    if not os.path.exists(aoi_shp):
        sys.exit(f"AOI shapefile not found: {aoi_shp}")
    print(f"AOI preset '{args.aoi}': {aoi_shp}")
    print(f"Run label: {run_label}")
    print(f"Modes: {modes}  Periods: {[_period_key(a, b) for a, b in periods]}")

    for mode_label in modes:
        for y1, y2 in periods:
            run_one(mode_label, y1, y2, aoi_shp, run_label, test=args.test)


if __name__ == "__main__":
    main()
