"""Run forest analysis for two AOI shapefiles for the 2021-2023 inventory period."""

import argparse
from pathlib import Path
from unittest.mock import patch

from config import OUTPUT_BASE_DIR, get_input_config


# Update these two shapefile paths before running.
AOI_SHAPEFILES = [
    r"C:\GIS\Data\LEARN\SourceData\AOI\AOI_TEST_1.shp",
    r"C:\GIS\Data\LEARN\SourceData\AOI\AOI_TEST_2.shp",
]


def _collect_dataset_paths(input_config):
    """Extract path-like string values from input configuration."""
    dataset_paths = []
    for key, value in input_config.items():
        if isinstance(value, str) and value and value != "None":
            if "\\" in value or "/" in value:
                dataset_paths.append((key, value))
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, str) and item and item != "None":
                    if "\\" in item or "/" in item:
                        dataset_paths.append((f"{key}[{idx}]", item))

    return dataset_paths


def log_dataset_paths(year1, year2, aoi_shapefile):
    """Print every dataset path expected to be used for this AOI run."""
    input_config = get_input_config(str(year1), str(year2))
    dataset_paths = _collect_dataset_paths(input_config)

    print("Dataset paths used for this run:")
    print(f"  - aoi_shapefile: {aoi_shapefile}")
    for key, path in dataset_paths:
        print(f"  - {key}: {path}")
    print(f"  - output_base_dir: {OUTPUT_BASE_DIR}")


def run_analysis_for_aoi(year1, year2, aoi_shapefile, id_field="FID", mode=None):
    """Run forests_analysis.main() for a specific AOI shapefile and inventory period."""
    import forests_analysis

    run_label = Path(aoi_shapefile).stem
    with patch("builtins.input", side_effect=[str(year1), str(year2)]):
        forests_analysis.main(
            mode=mode,
            aoi_shapefile=aoi_shapefile,
            id_field=id_field,
            run_label=run_label,
        )


def parse_args():
    """Parse command-line arguments for optional mode controls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["test", "recategorize"],
        default=None,
        help="Optional forests_analysis mode to pass through (e.g., recategorize).",
    )
    parser.add_argument(
        "--recat",
        "--recategorize",
        action="store_true",
        help="Shortcut for --mode recategorize.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    year1, year2 = 2021, 2023
    selected_mode = "recategorize" if args.recat else args.mode

    for aoi_shapefile in AOI_SHAPEFILES:
        print(
            f"\nRunning AOI test for {aoi_shapefile} ({year1}-{year2})"
            f" mode={selected_mode or 'default'}"
        )
        log_dataset_paths(year1, year2, aoi_shapefile)
        run_analysis_for_aoi(year1, year2, aoi_shapefile, mode=selected_mode)
