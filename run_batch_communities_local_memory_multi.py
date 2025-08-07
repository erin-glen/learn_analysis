import glob
import logging
import os
from typing import Dict, Iterable, Tuple

from config import OUTPUT_BASE_DIR
from run_communities import run_analysis_for_aoi_and_period

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Example mapping of GEOIDs to AOI names.
COMMUNITIES: Dict[str, str] = {
    "24031": "Montgomery",
    "51003": "Jefferson",
}

INVENTORY_PERIODS: Iterable[Tuple[int, int]] = [
    (2013, 2016),
]

TREE_CANOPY_SOURCE = "NLCD"
RECAT = True

def output_exists(aoi_name: str, year1: int, year2: int) -> bool:
    """Check if an output folder exists for the given AOI and period."""
    pattern = os.path.join(OUTPUT_BASE_DIR, f"{year1}_{year2}_{aoi_name}_*")
    return bool(glob.glob(pattern))

def main() -> None:
    total = len(COMMUNITIES) * len(list(INVENTORY_PERIODS))
    logging.info(f"Starting batch processing for {total} GEOID/inventory period combinations.")

    processed = []
    missing = []

    for geoid, aoi_name in COMMUNITIES.items():
        for year1, year2 in INVENTORY_PERIODS:
            logging.debug(f"Processing GEOID {geoid} for {year1}-{year2}.")
            try:
                run_analysis_for_aoi_and_period(year1, year2, aoi_name, TREE_CANOPY_SOURCE, RECAT)
                if output_exists(aoi_name, year1, year2):
                    processed.append((geoid, year1, year2))
                else:
                    missing.append((geoid, year1, year2))
            except Exception as exc:  # pylint: disable=broad-except
                logging.error("Failed GEOID %s %s-%s: %s", geoid, year1, year2, exc)
                missing.append((geoid, year1, year2))

    logging.info("Finished batch processing. %s processed, %s missing.", len(processed), len(missing))
    if missing:
        logging.info("Missing combinations:")
        for geoid, year1, year2 in missing:
            logging.info("  GEOID %s %s-%s", geoid, year1, year2)

if __name__ == "__main__":
    main()
