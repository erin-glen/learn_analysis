# disturbance_config.py

import os
import glob
import logging
from typing import Iterable, List, Sequence

# --------------------------------------------------------------------
# LOGGING CONFIG
# --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# --------------------------------------------------------------------
# GENERAL PATHS
# --------------------------------------------------------------------
BASE_DIR = r"C:\GIS\Data\LEARN\Disturbances"

# Insect/Disease
INSECT_GDB_DIR = os.path.join(BASE_DIR, "ADS")
INSECT_OUTPUT_DIR = os.path.join(INSECT_GDB_DIR, "Processed")
INSECT_FINAL_DIR = os.path.join(INSECT_GDB_DIR, "Final")

# Hansen (Hansen Global Forest Change harvest proxy)
HANSEN_INPUT_DIR = os.path.join(BASE_DIR, "Hansen")
HANSEN_OUTPUT_DIR = os.path.join(HANSEN_INPUT_DIR, "Processed")

# --------------------------------------------------------------------
# NLCD TREE CANOPY (TCC)
# --------------------------------------------------------------------
# Primary (v2023-5) and fallback (older v2021-4) locations/patterns
NLCD_TCC_INPUT_DIR = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_v2023-5_project"
NLCD_TCC_INPUT_DIR_FALLBACK = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_Project"

NLCD_TCC_YEARS = [2011, 2013, 2016, 2019, 2021, 2023]

# Filename patterns
NLCD_TCC_FILENAME_FMT = "nlcd_tcc_conus_wgs84_v2023-5_20230101_{year}1231_projected.tif"
NLCD_TCC_FILENAME_FMT_FALLBACK = "nlcd_tcc_conus_{year}_v2021-4_projected.tif"

def _tcc_candidates(year: int):
    return [
        os.path.join(NLCD_TCC_INPUT_DIR, NLCD_TCC_FILENAME_FMT.format(year=year)),
        os.path.join(NLCD_TCC_INPUT_DIR_FALLBACK, NLCD_TCC_FILENAME_FMT_FALLBACK.format(year=year)),
    ]

def _resolve_tcc_path(year: int) -> str:
    # Prefer exact matches
    for p in _tcc_candidates(year):
        if os.path.exists(p):
            return p
    # Fuzzy search as a last resort
    for base in [NLCD_TCC_INPUT_DIR, NLCD_TCC_INPUT_DIR_FALLBACK]:
        hits = glob.glob(os.path.join(base, f"*{year}*tcc*projected*.tif"))
        if hits:
            return hits[0]
    # Return the primary expected path (even if missing) so calling code can warn
    return _tcc_candidates(year)[0]

# Exposed mapping for code
NLCD_TCC_RASTERS = {y: _resolve_tcc_path(y) for y in NLCD_TCC_YEARS}

# Output dir for TCC change/severity
NLCD_TCC_OUTPUT_DIR = os.path.join(NLCD_TCC_INPUT_DIR, "Processed")

_missing_tcc = [y for y, p in NLCD_TCC_RASTERS.items() if not os.path.exists(p)]
if _missing_tcc:
    logging.warning("Missing NLCD Tree Canopy rasters for years: %s", _missing_tcc)

# Thresholds (upper bounds) for assigning canopy-loss severity classes 1-4
NLCD_TCC_SEVERITY_BREAKS = [25, 50, 75, 100]

# --------------------------------------------------------------------
# HARVEST WORKFLOW SELECTION
# --------------------------------------------------------------------
# Two harvest/other processing options are supported:
#   * "hansen"              => harvest_other.py
#   * "nlcd_tcc_severity"   => harvest_other_severity.py
# Update HARVEST_WORKFLOW to toggle between the legacy Hansen workflow
# and the newer NLCD Tree Canopy Cover severity workflow.
HARVEST_WORKFLOW = "hansen"

HARVEST_PRODUCTS = {
    "hansen": {
        "module": "harvest_other",
        "description": "Hansen Global Forest Change based harvest/other",
        "raster_directory": HANSEN_OUTPUT_DIR,
        "raster_template": "hansen_{period}.tif",
        "combined_output_subdir": "hansen",
    },
    "nlcd_tcc_severity": {
        "module": "harvest_other_severity",
        "description": "NLCD Tree Canopy Cover change severity",
        "raster_directory": NLCD_TCC_OUTPUT_DIR,
        "raster_template": "nlcd_tcc_severity_{period}.tif",
        "combined_output_subdir": "nlcd_tcc_severity",
    },
}


def harvest_product_config(workflow: str = None):
    """Return the harvest configuration for the selected workflow."""

    wf = workflow or HARVEST_WORKFLOW
    if wf not in HARVEST_PRODUCTS:
        raise ValueError(
            f"Unknown harvest workflow '{wf}'. Valid options: {sorted(HARVEST_PRODUCTS)}"
        )
    return dict(HARVEST_PRODUCTS[wf])


def harvest_raster_path(period_name: str, workflow: str = None) -> str:
    """Return the expected harvest raster path for the given period."""

    config = harvest_product_config(workflow)
    directory = config["raster_directory"]
    template = config["raster_template"]
    return os.path.join(directory, template.format(period=period_name))


def final_combined_dir(workflow: str = None) -> str:
    """Directory for final disturbance rasters for the selected harvest workflow."""

    config = harvest_product_config(workflow)
    combined_subdir = config.get("combined_output_subdir") or (workflow or HARVEST_WORKFLOW)
    path = os.path.join(FINAL_COMBINED_ROOT_DIR, combined_subdir)
    os.makedirs(path, exist_ok=True)
    return path

# --------------------------------------------------------------------
# NLCD LAND COVER (reference raster for env)
# --------------------------------------------------------------------
NLCD_LC_DIR = r"C:\GIS\Data\LEARN\SourceData\NEW_NLCD"
NLCD_LC_FILENAME_FMT = "Annual_NLCD_LndCov_{year}_CU_C1V0.tif"
NLCD_LC_YEARS = [2011, 2013, 2016, 2019, 2021, 2023]  # extend if you have more

def nlcd_lc_path(year: int) -> str:
    return os.path.join(NLCD_LC_DIR, NLCD_LC_FILENAME_FMT.format(year=year))

NLCD_LC_RASTERS = {y: nlcd_lc_path(y) for y in NLCD_LC_YEARS}

# Use 2021 LC as the default reference raster
NLCD_RASTER = NLCD_LC_RASTERS[2021]
if not os.path.exists(NLCD_RASTER):
    logging.warning("NLCD reference raster (2021 LC) not found at: %s", NLCD_RASTER)

# --------------------------------------------------------------------
# FIRE
# --------------------------------------------------------------------
FIRE_ROOT = os.path.join(BASE_DIR, "Fire", "Raw", "composite_data", "MTBS_BSmosaics")
FIRE_OUTPUT_DIR = os.path.join(BASE_DIR, "Fire", "Processed")

# --------------------------------------------------------------------
# FINAL COMBINATION OUTPUTS
# --------------------------------------------------------------------
INTERMEDIATE_COMBINED_DIR = os.path.join(BASE_DIR, "Intermediate")
FINAL_COMBINED_ROOT_DIR = os.path.join(BASE_DIR, "FinalCombined")
# Backwards compatibility: legacy callers may still reference FINAL_COMBINED_DIR
FINAL_COMBINED_DIR = FINAL_COMBINED_ROOT_DIR

# Create directories if they don’t exist:
_BASE_DIRS = [
    INSECT_OUTPUT_DIR,
    INSECT_FINAL_DIR,
    HANSEN_OUTPUT_DIR,
    NLCD_TCC_OUTPUT_DIR,
    FIRE_OUTPUT_DIR,
    INTERMEDIATE_COMBINED_DIR,
    FINAL_COMBINED_ROOT_DIR,
]

for _dir in _BASE_DIRS:
    os.makedirs(_dir, exist_ok=True)

for _cfg in HARVEST_PRODUCTS.values():
    combined_subdir = _cfg.get("combined_output_subdir") or ""
    if combined_subdir:
        os.makedirs(os.path.join(FINAL_COMBINED_ROOT_DIR, combined_subdir), exist_ok=True)

# --------------------------------------------------------------------
# REGIONS & TIME PERIODS
# --------------------------------------------------------------------
REGIONS = [1, 2, 3, 4, 6, 8, 9]

# Choose periods that match available TCC endpoints
TIME_PERIODS = {
    "2019_2021": [2019, 2021],
    # add more when corresponding TCC rasters are available
}

# --------------------------------------------------------------------
# HANSEN TILES
# --------------------------------------------------------------------
HANSEN_TILES = [
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_30N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_40N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_50N_130W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_070W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_080W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_090W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_100W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_110W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_120W.tif",
    r"C:\GIS\Data\LEARN\Disturbances\Hansen\GFW2023_60N_130W.tif",
]


def _hansen_tile_id(tile_path: str) -> str:
    """Return the bare tile identifier (filename without extension)."""

    base = os.path.basename(tile_path)
    stem, _ = os.path.splitext(base)
    return stem


_HANSEN_TILE_LOOKUP = {
    _hansen_tile_id(path).upper(): path
    for path in HANSEN_TILES
}


def normalize_tile_ids(tile_ids: Sequence[str]) -> List[str]:
    """Normalize user-provided Hansen tile identifiers."""

    normed: List[str] = []
    for raw in tile_ids:
        if raw is None:
            continue
        tile = raw.strip().upper()
        if not tile:
            continue
        tile = tile.replace(".TIF", "")
        if tile in _HANSEN_TILE_LOOKUP:
            normed.append(tile)
            continue
        prefixed = tile if tile.startswith("GFW") else f"GFW2023_{tile.lstrip('_')}"
        normed.append(prefixed)
    return normed


def hansen_tile_paths(tile_ids: Iterable[str] | None = None) -> List[str]:
    """Return Hansen tile paths filtered by ``tile_ids`` if provided."""

    if not tile_ids:
        return list(HANSEN_TILES)

    selected: List[str] = []
    missing: List[str] = []
    for tile in normalize_tile_ids(tile_ids):
        path = _HANSEN_TILE_LOOKUP.get(tile)
        if path:
            selected.append(path)
        else:
            missing.append(tile)

    if missing:
        logging.warning(
            "Ignored %d Hansen tile id(s) with no match: %s",
            len(missing),
            ", ".join(sorted(set(missing))),
        )

    # Deduplicate while preserving order of input sequence
    seen = set()
    ordered: List[str] = []
    for path in selected:
        if path not in seen:
            ordered.append(path)
            seen.add(path)

    if not ordered:
        logging.error("No Hansen tiles matched the provided ids. Using full tile list instead.")
        return list(HANSEN_TILES)

    logging.info("Selected %d Hansen tile(s).", len(ordered))
    for path in ordered:
        logging.info("  %s", path)

    return ordered