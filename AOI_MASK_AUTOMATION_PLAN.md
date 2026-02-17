# AOI Mask Automation Plan for `qa_harvest_loss_distribution`

## Goal
Automate AOI mask creation per inventory period so the QA workflow no longer relies on a manually supplied `--mask` input, and instead uses a **forest-remains-forest** mask derived from NLCD land cover years bracketing each period.

## Current gap
`qa_harvest_loss_distribution.py` supports optional `--mask` / `--mask0-outside` inputs, but it does not generate period-specific AOI masks from NLCD land-cover transitions.

## Proposed target behavior
For each analyzed period (for example `2016_2019`):
1. Build a binary raster where cells are `1` only if they are forest in both start and end NLCD years.
2. Save/reuse this AOI mask as a period artifact.
3. Apply that AOI mask automatically during zonal histogram QA for the period.
4. Allow optional override to keep backwards compatibility with manual masks.

## Implementation design

### 1) Add configuration for AOI outputs and class definitions
Add the following to `disturbance_config.py`:
- `NLCD_AOI_MASK_DIR` (new output folder, e.g., `<NLCD_HARVEST_ROOT>/AOI_masks`)
- `NLCD_FOREST_CLASSES` (list/set of NLCD class codes treated as forest)
- Optional `AOI_MASK_BUILD_MODE` with default `"forest_remains_forest"`

Why: keeps forest class logic and output paths centralized and consistent with existing pipeline conventions.

### 2) Build period AOI mask from NLCD start/end land cover
Add helper functions to `qa_harvest_loss_distribution.py` (or a small shared utility module if reuse is expected):
- `_forest_binary(lc_raster)` -> raster with `1` for configured forest classes, NoData otherwise.
- `_build_period_aoi_mask(period, force=False)` ->
  - Parse period into start/end years.
  - Load `cfg.NLCD_LC_RASTERS[start]` and `[end]`.
  - Compute `forest_start` and `forest_end`.
  - Compute intersection: `Con((forest_start == 1) & (forest_end == 1), 1)`.
  - Save to `NLCD_AOI_MASK_DIR/aoi_forest_remaining_forest_<period>.tif`.
  - Return mask raster path.
- `_resolve_period_mask(period, args)` -> choose precedence:
  1) explicit user `--mask` (legacy override), else
  2) auto-generated period AOI mask.

### 3) Integrate AOI generation into main loop
In the existing period loop:
- Resolve `period_mask_obj` once per period.
- Run `ZonalHistogram` in `EnvManager(mask=period_mask_obj)`.
- Log whether mask was user-supplied or auto-generated.

This keeps behavior deterministic and period-specific.

### 4) Add CLI controls for controlled rollout
Add arguments:
- `--auto-aoi` (default `True`) to enable automatic per-period AOI masks.
- `--aoi-force-rebuild` to rebuild masks even if files already exist.
- `--no-auto-aoi` to disable and preserve current unmasked behavior (unless `--mask` provided).

Suggested precedence:
- If `--mask` is provided, use it.
- Else if `--auto-aoi` is enabled, use generated period mask.
- Else, run without mask.

### 5) Cache and validation strategy
- Reuse existing AOI mask TIFF if present, unless `--aoi-force-rebuild` is set.
- Validate mask alignment against `cfg.NLCD_RASTER` (snap, cell size, extent, spatial reference).
- If start/end NLCD LC raster missing for a period:
  - default behavior: log warning and skip that period,
  - optional strict mode: fail fast.

### 6) QA and diagnostics outputs
Extend diagnostics under `loss_threshold_QA`:
- Add columns in output CSVs:
  - `aoi_source` (`manual`, `auto_forest_remaining_forest`, `none`)
  - `aoi_mask_path`
- Add optional AOI diagnostics CSV:
  - period, AOI cells, AOI area (ha), AOI share of reference extent (%).

This makes it easy to verify masks changed as expected across periods.

## Suggested phased rollout

### Phase 1 (minimum viable)
- Add config constants.
- Implement per-period AOI generation.
- Use auto AOI in QA loop by default.
- Keep manual `--mask` override.

### Phase 2 (hardening)
- Add strict/missing-data handling mode.
- Add AOI diagnostics CSV and richer logging.
- Add simple unit-style helpers for period parsing and forest-class mapping logic where practical.

### Phase 3 (pipeline reuse)
- Move AOI builder to shared utility module so other scripts (e.g., severity or final disturbance QA) can consume identical AOI masks.
- Optionally precompute all period AOIs in a dedicated preprocessing script.

## Acceptance criteria
1. Running QA with default args automatically creates/uses one AOI mask per analyzed period.
2. AOI mask raster exists on disk for each processed period and is aligned to NLCD reference grid.
3. Output CSVs record AOI source/path per period.
4. Manual `--mask` still works and overrides auto AOI.
5. Re-running without `--aoi-force-rebuild` reuses masks (no unnecessary recompute).

## Risks / open decisions
- Confirm exact NLCD forest class list for your project definition (for example, deciduous/evergreen/mixed plus woody wetlands if desired).
- Confirm whether AOI should include only CONUS forest, or also ownership constraints (USFS/BLM) in future.
- Confirm handling for periods where either endpoint NLCD LC is unavailable.
