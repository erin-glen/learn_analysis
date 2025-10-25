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
# NLCD TREE CANOPY (TCC) INPUTS
# --------------------------------------------------------------------
# Primary (v2023-5) and fallback (older v2021-4) locations/patterns
NLCD_TCC_INPUT_DIR = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_v2023-5_project"
NLCD_TCC_INPUT_DIR_FALLBACK = r"C:\GIS\Data\LEARN\SourceData\TreeCanopy\NLCD_Project"

# Candidate years (extend if you add more)
NLCD_TCC_YEARS = [2011, 2013, 2016, 2019, 2021, 2023]

# Exact filename patterns
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
    # Fuzzy search (helps if filenames differ slightly)
    for base in [NLCD_TCC_INPUT_DIR, NLCD_TCC_INPUT_DIR_FALLBACK]:
        hits = glob.glob(os.path.join(base, f"*{year}*tcc*projected*.tif"))
        if hits:
            return hits[0]
    # Return the primary expected path (even if missing) so callers can warn/skip gracefully
    return _tcc_candidates(year)[0]

# Exposed mapping for code
NLCD_TCC_RASTERS = {y: _resolve_tcc_path(y) for y in NLCD_TCC_YEARS}

_missing_tcc = [y for y, p in NLCD_TCC_RASTERS.items() if not os.path.exists(p)]
if _missing_tcc:
    logging.warning("Missing NLCD Tree Canopy rasters for years: %s", _missing_tcc)

# --------------------------------------------------------------------
# NLCD LAND COVER (reference raster for env)
# --------------------------------------------------------------------
NLCD_LC_DIR = r"C:\GIS\Data\LEARN\SourceData\NEW_NLCD"
NLCD_LC_FILENAME_FMT = "Annual_NLCD_LndCov_{year}_CU_C1V0.tif"
NLCD_LC_YEARS = [2011, 2013, 2016, 2019, 2021, 2023]  # extend if needed

def nlcd_lc_path(year: int) -> str:
    return os.path.join(NLCD_LC_DIR, NLCD_LC_FILENAME_FMT.format(year=year))

NLCD_LC_RASTERS = {y: nlcd_lc_path(y) for y in NLCD_LC_YEARS}

# Use 2021 LC as the default reference raster
NLCD_RASTER = NLCD_LC_RASTERS[2021]
if not os.path.exists(NLCD_RASTER):
    logging.warning("NLCD reference raster (2021 LC) not found at: %s", NLCD_RASTER)

# --------------------------------------------------------------------
# CENTRALIZED NLCD HARVEST OUTPUT FOLDERS
# --------------------------------------------------------------------
NLCD_HARVEST_ROOT = r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity"

# Final disturbance outputs (combined; 1–4 harvest, 5 insect, 10 fire)
NLCD_FINAL_DIR = os.path.join(NLCD_HARVEST_ROOT, "final_disturbances")

# “What will be counted as harvest” after masking out fire/insect (1–4 only)
NLCD_FINAL_HARVEST_ONLY_DIR = os.path.join(NLCD_HARVEST_ROOT, "1-4")

# Convenience presence layers
NLCD_FINAL_INSECT_DIR = os.path.join(NLCD_HARVEST_ROOT, "5")
NLCD_FINAL_FIRE_DIR   = os.path.join(NLCD_HARVEST_ROOT, "10")

# NLCD TCC-based derivations (not masked by other layers)
NLCD_TCC_CHANGE_DIR       = os.path.join(NLCD_HARVEST_ROOT, "Tree_canopy_change")       # absolute pp change
NLCD_TCC_PCT_CHANGE_DIR   = os.path.join(NLCD_HARVEST_ROOT, "Tree_canopy_pct_change")   # percent change
NLCD_HARVEST_SEVERITY_DIR = os.path.join(NLCD_HARVEST_ROOT, "Harvest_severity")         # pp-based severity (0–4)
NLCD_HARVEST_PCT_SEV_DIR  = os.path.join(NLCD_HARVEST_ROOT, "Harvest_pct_severity")     # pct-based severity (0–4)

for _d in [
    NLCD_HARVEST_ROOT,
    NLCD_FINAL_DIR,
    NLCD_FINAL_HARVEST_ONLY_DIR,
    NLCD_FINAL_INSECT_DIR,
    NLCD_FINAL_FIRE_DIR,
    NLCD_TCC_CHANGE_DIR,
    NLCD_TCC_PCT_CHANGE_DIR,
    NLCD_HARVEST_SEVERITY_DIR,
    NLCD_HARVEST_PCT_SEV_DIR,
]:
    os.makedirs(_d, exist_ok=True)

# --------------------------------------------------------------------
# SEVERITY & PROCESSING KNOBS
# --------------------------------------------------------------------
# Severity thresholds (upper bounds) for pp-based severity 1–4
NLCD_TCC_SEVERITY_BREAKS = [25, 50, 75, 100]

# Percent-based severity thresholds (falls back to pp thresholds if not set)
NLCD_TCC_PCT_SEVERITY_BREAKS = [25, 50, 75, 100]

# Minimum starting canopy (%) for percent change to avoid unstable ratios
NLCD_TCC_PCT_MIN_BASE = 0  # e.g., set to 1 or 5 if desired

# Fire masking (preserve harvest=3 downstream)
MASK_LOW_SEVERITY_FIRE = True
FIRE_LOW_SEVERITY_CODE = 3

# Performance toggles
PARALLEL_PROCESSING_FACTOR = "90%"  # "100%" or integer cores, e.g., "8"
COMPUTE_OUTPUT_STATS = False        # avoid CalculateStatistics for speed
WRITE_PP_CHANGE = True              # write nlcd_tcc_change_*.tif
WRITE_PCT_CHANGE = True             # write nlcd_tcc_pct_change_*.tif
SCRATCH_WORKSPACE = r""             # e.g., r"D:\arcgis_scratch" on a fast SSD

# --------------------------------------------------------------------
# HARVEST WORKFLOW SELECTION
# --------------------------------------------------------------------
# Supported:
#   * "hansen"                    => legacy Hansen harvest
#   * "nlcd_tcc_severity"         => NLCD TCC pp-based severity (recommended)
#   * "nlcd_tcc_percent_severity" => NLCD TCC percent-based severity
HARVEST_WORKFLOW = "nlcd_tcc_severity"

HARVEST_PRODUCTS = {
    "hansen": {
        "module": "harvest_other",
        "description": "Hansen Global Forest Change based harvest/other",
        "raster_directory": HANSEN_OUTPUT_DIR,
        "raster_template": "hansen_{period}.tif",
        "method_tag": "hansen",
    },
    "nlcd_tcc_severity": {
        "module": "harvest_other_severity",
        "description": "NLCD Tree Canopy Cover change severity (pp-based)",
        "raster_directory": NLCD_HARVEST_SEVERITY_DIR,
        "raster_template": "nlcd_tcc_severity_{period}.tif",
        "method_tag": "abs",
    },
    "nlcd_tcc_percent_severity": {
        "module": "harvest_other_severity_percent",
        "description": "NLCD Tree Canopy Cover percent-change severity",
        "raster_directory": NLCD_HARVEST_PCT_SEV_DIR,
        "raster_template": "nlcd_tcc_pct_severity_{period}.tif",
        "method_tag": "pct",
    },
}

def harvest_product_config(workflow: str | None = None):
    wf = (workflow or HARVEST_WORKFLOW).lower()
    if wf not in HARVEST_PRODUCTS:
        raise ValueError(f"Unknown harvest workflow '{wf}'. Valid: {sorted(HARVEST_PRODUCTS)}")
    return dict(HARVEST_PRODUCTS[wf])

def harvest_raster_path(period_name: str, workflow: str | None = None) -> str:
    cfg_ = harvest_product_config(workflow)
    return os.path.join(cfg_["raster_directory"], cfg_["raster_template"].format(period=period_name))

def final_combined_dir(workflow: str | None = None) -> str:
    # All methods write combined finals here; method appears in filename (disturb_{abs|pct|hansen}_{period}.tif)
    return NLCD_FINAL_DIR

# --------------------------------------------------------------------
# FIRE
# --------------------------------------------------------------------
FIRE_ROOT = os.path.join(BASE_DIR, "Fire", "Raw", "composite_data", "MTBS_BSmosaics")
FIRE_OUTPUT_DIR = os.path.join(BASE_DIR, "Fire", "Processed")

# --------------------------------------------------------------------
# LEGACY OUTPUT ROOTS (back-compat)
# --------------------------------------------------------------------
INTERMEDIATE_COMBINED_DIR = os.path.join(BASE_DIR, "Intermediate")
FINAL_COMBINED_ROOT_DIR = os.path.join(BASE_DIR, "FinalCombined")
# Keep legacy symbol pointing to the new final directory for compatibility
FINAL_COMBINED_DIR = NLCD_FINAL_DIR

for _d in [INSECT_OUTPUT_DIR, INSECT_FINAL_DIR, HANSEN_OUTPUT_DIR, FIRE_OUTPUT_DIR,
           INTERMEDIATE_COMBINED_DIR, FINAL_COMBINED_ROOT_DIR]:
    os.makedirs(_d, exist_ok=True)

# --------------------------------------------------------------------
# REGIONS & TIME PERIODS
# --------------------------------------------------------------------
REGIONS = [1, 2, 3, 4, 6, 8, 9]

def _available_tcc_years() -> List[int]:
    """Years with resolvable TCC rasters on disk."""
    yrs: List[int] = []
    for y in sorted(NLCD_TCC_YEARS):
        p = NLCD_TCC_RASTERS.get(y)
        if p and os.path.exists(p):
            yrs.append(y)
    if len(yrs) < 2:
        logging.warning("Fewer than two available TCC years found: %s", yrs)
    else:
        logging.info("Available TCC years on disk: %s", yrs)
    return yrs

def _build_adjacent_periods(yrs: Sequence[int]) -> dict:
    """Build adjacent periods only where both endpoints exist."""
    periods: dict[str, list[int]] = {}
    for a, b in zip(yrs, yrs[1:]):
        if os.path.exists(NLCD_TCC_RASTERS.get(a, "")) and os.path.exists(NLCD_TCC_RASTERS.get(b, "")):
            periods[f"{a}_{b}"] = [a, b]
    if not periods:
        logging.warning("No adjacent TCC periods could be built from: %s", yrs)
    else:
        logging.info("Discovered TCC periods: %s", ", ".join(periods.keys()))
    return periods

# Auto-generate TIME_PERIODS from available TCC years
TIME_PERIODS = _build_adjacent_periods(_available_tcc_years())

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
    base = os.path.basename(tile_path)
    stem, _ = os.path.splitext(base)
    return stem

_HANSEN_TILE_LOOKUP = {_hansen_tile_id(path).upper(): path for path in HANSEN_TILES}

def normalize_tile_ids(tile_ids: Sequence[str]) -> List[str]:
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
        logging.warning("Ignored %d Hansen tile id(s) with no match: %s",
                        len(missing), ", ".join(sorted(set(missing))))

    # Deduplicate while preserving order
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
