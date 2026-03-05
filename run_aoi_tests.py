"""Run forest analysis for two AOI shapefiles for the 2021-2023 inventory period."""

import argparse
from pathlib import Path
from unittest.mock import patch

import forests_analysis


# Update these two shapefile paths before running.
AOI_SHAPEFILES = [
    r"C:\GIS\Data\LEARN\SourceData\AOI\AOI_TEST_1.shp",
    r"C:\GIS\Data\LEARN\SourceData\AOI\AOI_TEST_2.shp",
]


def parse_args():
    """Parse optional CLI settings for test mode and recategorization mode."""
    parser = argparse.ArgumentParser(
        description=(
            "Run forests_analysis for configured AOI shapefiles for the 2021-2023 inventory period."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["recategorize", "test"],
        default=None,
        help="Optional forests_analysis mode to pass through.",
    )
    parser.add_argument(
        "--recategorize",
        action="store_true",
        help="Shortcut for --mode recategorize.",
    )
    return parser.parse_args()


def run_analysis_for_aoi(year1, year2, aoi_shapefile, id_field="FID", mode=None):
    """Run forests_analysis.main() for a specific AOI shapefile and inventory period."""
    run_label = Path(aoi_shapefile).stem
    with patch("builtins.input", side_effect=[str(year1), str(year2)]):
        forests_analysis.main(
            mode=mode,
            aoi_shapefile=aoi_shapefile,
            id_field=id_field,
            run_label=run_label,
        )


if __name__ == "__main__":
    args = parse_args()

    year1, year2 = 2021, 2023
    selected_mode = "recategorize" if args.recategorize else args.mode

    mode_label = selected_mode if selected_mode is not None else "default"
    print(f"Using mode: {mode_label}")

    for aoi_shapefile in AOI_SHAPEFILES:
        print(f"\nRunning AOI test for {aoi_shapefile} ({year1}-{year2}), mode={mode_label}")
        run_analysis_for_aoi(year1, year2, aoi_shapefile, mode=selected_mode)
