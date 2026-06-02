"""
Summarize the Forest Remaining Forest portion of run_padus_forests.py outputs
across the dimensions most useful for downstream reporting.

Reads the tagged + AOI-joined dataset produced by compile_padus_results.py and
filters Category == "Forest Remaining Forest" (Fire, Harvest 0-25%/25-50%/50-75%/
75-100%, Insect/Disease, Undisturbed). Writes summary CSVs to

    <BASE_DIR>/summaries_FRF/

Outputs (long format, one row per recat_mode x inventory_period x dimension x type;
each summary also has a `_wide` companion with Type pivoted into columns):

    frf_totals.csv               national totals (mode, period, type)
    frf_by_unit.csv              per-unit (with mang_name/unit_nm/state_nm/gap_sts/acres_nat/fs_region)
    frf_by_protection.csv        by GAP status
    frf_by_agency.csv            by USFS vs BLM
    frf_by_state.csv             by state
    frf_by_fs_region.csv         by Forest Service Region (USFS); BLM lumped as 'BLM'

Each row carries:
    area_ha             - total Forest Remaining Forest area in that disturbance type
    flux_tCO2e_yr       - net GHG flux (negative = sink, positive = source)
    flux_per_ha         - flux per hectare (tCO2e / ha / yr)

A parallel `frf_learn_<dim>.csv` is also written for each dimension above, in the
LEARN GHG-inventory report format. Each (mode, period, AOI) emits all 7 LEARN
types (Undisturbed Forest; Disturbed Forest: Fire / Insect/Disease; Harvest: High /
High Moderate / Low Moderate / Low) with Area (ha), Removals (t CO2e/yr) for
Undisturbed only, Emissions (t CO2e/yr) for the disturbance types, and the
area-weighted Factor (tC/ha for emissions, tC/ha/yr for removals).

Usage (any python with pandas + pyogrio):
    python summarize_padus_forests.py
    python summarize_padus_forests.py --rebuild      # force re-run compile_padus_results.py
    python summarize_padus_forests.py --out /tmp/x   # custom output dir
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import compile_padus_results as compile_mod

BASE_DIR = compile_mod.BASE_DIR
COMPILED_CSV = compile_mod.FINAL_OUTPUT
DEFAULT_OUTPUT_DIR = BASE_DIR / "summaries_FRF"

FRF_CATEGORY = "Forest Remaining Forest"
KEY_COLS = ["recat_mode", "inventory_period"]
TYPE_COL = "Type"
AREA_COL = "Area (ha, total)"
FLUX_COL = "GHG Flux (t CO2e/year)"

# LEARN-format conventions
C_TO_CO2 = 44 / 12
INVENTORY_PERIOD_YEARS = {
    "2013_2016": 3,
    "2016_2019": 3,
    "2019_2021": 2,
    "2021_2023": 2,
}
TYPE_TO_LEARN = {
    "Undisturbed":      "Undisturbed Forest",
    "Fire":             "Disturbed Forest: Fire",
    "Insect/Disease":   "Disturbed Forest: Insect/Disease",
    "Harvest 75-100%":  "Harvest: High",
    "Harvest 50-75%":   "Harvest: High Moderate",
    "Harvest 25-50%":   "Harvest: Low Moderate",
    "Harvest 0-25%":    "Harvest: Low",
}
LEARN_TYPE_ORDER = [
    "Undisturbed Forest",
    "Disturbed Forest: Fire",
    "Disturbed Forest: Insect/Disease",
    "Harvest: High",
    "Harvest: High Moderate",
    "Harvest: Low Moderate",
    "Harvest: Low",
]
LEARN_REMOVAL_TYPES = {"Undisturbed Forest"}  # factor unit = tC/ha/yr; everything else = tC/ha

# State-to-FS-Region mapping (applies to USFS units only).
# ID (R1/R4) and WY (R2/R4) span two regions in reality; the assignments below
# pick the dominant region by national-forest acreage. Edit if a different
# convention is needed for a specific analysis.
STATE_TO_FS_REGION = {
    # R1 Northern
    "MT": "R1", "ND": "R1", "ID": "R1",  # ID also has R4 forests (Sawtooth, Boise, Payette, Caribou-Targhee)
    # R2 Rocky Mountain
    "CO": "R2", "KS": "R2", "NE": "R2", "SD": "R2", "WY": "R2",  # WY also has R4 (Bridger-Teton)
    # R3 Southwestern
    "AZ": "R3", "NM": "R3",
    # R4 Intermountain
    "NV": "R4", "UT": "R4",
    # R5 Pacific Southwest
    "CA": "R5", "HI": "R5",
    # R6 Pacific Northwest
    "OR": "R6", "WA": "R6",
    # R8 Southern
    "AL": "R8", "AR": "R8", "FL": "R8", "GA": "R8", "KY": "R8", "LA": "R8",
    "MS": "R8", "NC": "R8", "OK": "R8", "PR": "R8", "SC": "R8", "TN": "R8",
    "TX": "R8", "VA": "R8",
    # R9 Eastern
    "CT": "R9", "DE": "R9", "IL": "R9", "IN": "R9", "IA": "R9", "ME": "R9",
    "MD": "R9", "MA": "R9", "MI": "R9", "MN": "R9", "MO": "R9", "NH": "R9",
    "NJ": "R9", "NY": "R9", "OH": "R9", "PA": "R9", "RI": "R9", "VT": "R9",
    "WV": "R9", "WI": "R9",
    # R10 Alaska
    "AK": "R10",
}


def add_fs_region(df: pd.DataFrame) -> pd.DataFrame:
    """Add fs_region column. USFS units mapped from state; BLM units lumped as 'BLM'."""
    if "mang_name" not in df.columns or "state_nm" not in df.columns:
        return df
    df = df.copy()
    is_usfs = df["mang_name"].eq("USFS")
    df["fs_region"] = df["state_nm"].map(STATE_TO_FS_REGION)
    df.loc[~is_usfs, "fs_region"] = "BLM"
    df["fs_region"] = df["fs_region"].fillna("UNKNOWN")
    return df


def ensure_compiled(rebuild: bool) -> Path:
    """Run compile_padus_results.main() if the compiled CSV is missing or stale."""
    needs_rebuild = rebuild or not COMPILED_CSV.exists()
    if not needs_rebuild:
        compiled_mtime = COMPILED_CSV.stat().st_mtime
        latest_input = 0.0
        for mode in compile_mod.RECAT_MODES:
            for period in compile_mod.INVENTORY_PERIODS:
                csv_path, _ = compile_mod.find_run_csv(mode, period)
                if csv_path is not None:
                    latest_input = max(latest_input, csv_path.stat().st_mtime)
        if latest_input > compiled_mtime:
            print(f"Compiled CSV is older than newest combined_results.csv; rebuilding.")
            needs_rebuild = True
    if needs_rebuild:
        print("Running compile_padus_results.main() ...")
        compile_mod.main()
    return COMPILED_CSV


def load_frf(compiled_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(compiled_csv, low_memory=False)
    df = df[df["Category"] == FRF_CATEGORY].copy()
    if df.empty:
        raise SystemExit(f"No '{FRF_CATEGORY}' rows in {compiled_csv}")
    df = df.rename(columns={AREA_COL: "area_ha", FLUX_COL: "flux_tCO2e_yr"})
    df = add_fs_region(df)
    return df


def _summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    grouped = (
        df.groupby(group_cols, dropna=False, as_index=False)
          .agg(area_ha=("area_ha", "sum"),
               flux_tCO2e_yr=("flux_tCO2e_yr", "sum"))
    )
    grouped["flux_per_ha"] = (grouped["flux_tCO2e_yr"]
                              / grouped["area_ha"].where(grouped["area_ha"] != 0))
    return grouped


def _pivot_type_to_columns(long_df: pd.DataFrame, dim_cols: list[str]) -> pd.DataFrame:
    """Pivot Type into columns; one row per (mode, period, *dim_cols)."""
    idx = KEY_COLS + dim_cols
    wide_area = long_df.pivot_table(
        index=idx, columns=TYPE_COL, values="area_ha", aggfunc="sum", fill_value=0)
    wide_flux = long_df.pivot_table(
        index=idx, columns=TYPE_COL, values="flux_tCO2e_yr", aggfunc="sum", fill_value=0)
    wide_area.columns = [f"area_ha__{c}" for c in wide_area.columns]
    wide_flux.columns = [f"flux_tCO2e_yr__{c}" for c in wide_flux.columns]
    out = wide_area.join(wide_flux).reset_index()
    out["area_ha__TOTAL"] = wide_area.sum(axis=1).values
    out["flux_tCO2e_yr__TOTAL"] = wide_flux.sum(axis=1).values
    return out


def _learn_long(df: pd.DataFrame, dim_cols: list[str]) -> pd.DataFrame:
    """Aggregate to LEARN-report format: one row per (mode, period, *dim_cols, LEARN_Type).

    All 7 LEARN types are emitted for every (mode, period, AOI) combo (zero-filled if absent),
    so each AOI's rows can be filtered out as a complete LEARN inventory table.

    Factor is the area-weighted EF reconstructed from the aggregated flux:
        Removals (Undisturbed): tC/ha/yr  = flux_yr / area / (44/12)
        Emissions (else):       tC/ha     = flux_yr * years / area / (44/12)
    Factor is NaN when area is 0.
    """
    work = df.copy()
    work["LEARN_Type"] = work[TYPE_COL].map(TYPE_TO_LEARN)
    work = work.dropna(subset=["LEARN_Type"])

    grouped = (
        work.groupby(KEY_COLS + dim_cols + ["LEARN_Type"], dropna=False, as_index=False)
            .agg(area_ha=("area_ha", "sum"),
                 flux_tCO2e_yr=("flux_tCO2e_yr", "sum"))
    )

    # Reindex so every (mode, period, dim) combo has all 7 LEARN types.
    base_keys = grouped[KEY_COLS + dim_cols].drop_duplicates()
    full = base_keys.merge(pd.DataFrame({"LEARN_Type": LEARN_TYPE_ORDER}), how="cross")
    grouped = full.merge(grouped, on=KEY_COLS + dim_cols + ["LEARN_Type"], how="left")
    grouped["area_ha"] = grouped["area_ha"].fillna(0)
    grouped["flux_tCO2e_yr"] = grouped["flux_tCO2e_yr"].fillna(0)

    is_removal = grouped["LEARN_Type"].isin(LEARN_REMOVAL_TYPES)
    grouped["Removals (t CO2e/yr)"] = np.where(is_removal, grouped["flux_tCO2e_yr"], np.nan)
    grouped["Emissions (t CO2e/yr)"] = np.where(~is_removal, grouped["flux_tCO2e_yr"], np.nan)

    years = grouped["inventory_period"].map(INVENTORY_PERIOD_YEARS).astype(float)
    safe_area = grouped["area_ha"].where(grouped["area_ha"] > 0)
    factor = np.where(
        is_removal,
        grouped["flux_tCO2e_yr"] / safe_area / C_TO_CO2,
        grouped["flux_tCO2e_yr"] * years / safe_area / C_TO_CO2,
    )
    grouped["Factor (t C/ha for emissions, t C/ha/yr for removals)"] = factor

    sort_order = {t: i for i, t in enumerate(LEARN_TYPE_ORDER)}
    grouped["__sort"] = grouped["LEARN_Type"].map(sort_order)
    grouped = (grouped
               .sort_values(KEY_COLS + dim_cols + ["__sort"])
               .drop(columns=["__sort", "flux_tCO2e_yr"])
               .rename(columns={"area_ha": "Area (ha, total)"}))
    return grouped[KEY_COLS + dim_cols + [
        "LEARN_Type",
        "Area (ha, total)",
        "Removals (t CO2e/yr)",
        "Emissions (t CO2e/yr)",
        "Factor (t C/ha for emissions, t C/ha/yr for removals)",
    ]]


def write_learn(name: str, learn_df: pd.DataFrame, out_dir: Path) -> None:
    path = out_dir / f"{name}.csv"
    learn_df.to_csv(path, index=False)
    print(f"  wrote {path.name} ({len(learn_df):,} rows)")


def write_pair(name: str, long_df: pd.DataFrame, dim_cols: list[str], out_dir: Path) -> None:
    long_path = out_dir / f"{name}.csv"
    wide_path = out_dir / f"{name}_wide.csv"
    long_df.to_csv(long_path, index=False)
    _pivot_type_to_columns(long_df, dim_cols).to_csv(wide_path, index=False)
    print(f"  wrote {long_path.name} ({len(long_df):,} rows)")
    print(f"  wrote {wide_path.name}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rebuild", action="store_true",
                   help="Force re-run of compile_padus_results.main() before summarizing.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    args = p.parse_args()

    compiled_csv = ensure_compiled(args.rebuild)
    frf = load_frf(compiled_csv)

    args.out.mkdir(parents=True, exist_ok=True)

    print(f"\nForest Remaining Forest rows: {len(frf):,}")
    print(f"  recat_modes:         {sorted(frf['recat_mode'].unique())}")
    print(f"  inventory_periods:   {sorted(frf['inventory_period'].unique())}")
    print(f"  Forest Remaining Forest types: {sorted(frf[TYPE_COL].unique())}")
    print(f"  unique units (Geography_ID):   {frf['Geography_ID'].nunique()}")
    print()

    # 1. National totals (mode x period x type)
    write_pair("frf_totals",
               _summarize(frf, KEY_COLS + [TYPE_COL]),
               dim_cols=[],
               out_dir=args.out)
    write_learn("frf_learn_totals", _learn_long(frf, []), args.out)

    # 2. Per-unit (with attribute columns for downstream filtering)
    unit_cols = ["Geography_ID", "mang_name", "unit_nm", "state_nm",
                 "gap_sts", "acres_nat", "fs_region"]
    unit_cols = [c for c in unit_cols if c in frf.columns]
    write_pair("frf_by_unit",
               _summarize(frf, KEY_COLS + unit_cols + [TYPE_COL]),
               dim_cols=unit_cols,
               out_dir=args.out)
    write_learn("frf_learn_by_unit", _learn_long(frf, unit_cols), args.out)

    # 3. By GAP protection status
    if "gap_sts" in frf.columns:
        write_pair("frf_by_protection",
                   _summarize(frf, KEY_COLS + ["gap_sts", TYPE_COL]),
                   dim_cols=["gap_sts"],
                   out_dir=args.out)
        write_learn("frf_learn_by_protection", _learn_long(frf, ["gap_sts"]), args.out)

    # 4. By agency (USFS vs BLM)
    if "mang_name" in frf.columns:
        write_pair("frf_by_agency",
                   _summarize(frf, KEY_COLS + ["mang_name", TYPE_COL]),
                   dim_cols=["mang_name"],
                   out_dir=args.out)
        write_learn("frf_learn_by_agency", _learn_long(frf, ["mang_name"]), args.out)

    # 5. By state
    if "state_nm" in frf.columns:
        write_pair("frf_by_state",
                   _summarize(frf, KEY_COLS + ["state_nm", TYPE_COL]),
                   dim_cols=["state_nm"],
                   out_dir=args.out)
        write_learn("frf_learn_by_state", _learn_long(frf, ["state_nm"]), args.out)

    # 6. By FS Region (USFS state-based, BLM lumped)
    if "fs_region" in frf.columns:
        write_pair("frf_by_fs_region",
                   _summarize(frf, KEY_COLS + ["fs_region", TYPE_COL]),
                   dim_cols=["fs_region"],
                   out_dir=args.out)
        write_learn("frf_learn_by_fs_region", _learn_long(frf, ["fs_region"]), args.out)

    # On-screen sanity print
    totals = _summarize(frf, KEY_COLS)
    totals["flux_MtCO2e_yr"] = totals["flux_tCO2e_yr"] / 1e6
    print("\nFRF national totals (all types combined):")
    print(totals.to_string(index=False))


if __name__ == "__main__":
    main()
