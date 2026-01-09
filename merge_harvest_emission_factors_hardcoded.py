#!/usr/bin/env python3
"""
merge_harvest_emission_factors_hardcoded.py

Deterministically merges disaggregated (severity-bin) harvest emission factors from an
Excel workbook into an existing lookup-table CSV.

This variant hardcodes:
  1) The old lookup CSV path
  2) The updated factors XLSX path
  3) The output directory

All other paths (crosswalk path, output CSV name, audit directory, etc.) are created
automatically from those settings.

What you edit
-------------
Edit only the three settings in the "USER SETTINGS" section near the top of this file.

Run
---
python merge_harvest_emission_factors_hardcoded.py

Outputs
-------
In OUTPUT_DIR, the script writes:
- <old_csv_stem>_with_new_harvest_EFs.csv
- audit_harvest_merge/
    - audit_summary.txt
    - unmapped_crosswalk_rows.csv (if any unmapped rows exist; allowed blank/zero rows still logged)
    - missing_factor_rows.csv (written only if there is an error)
    - fallback_factors_filled.csv (if fallback applied)
    - fallback_old_rows_affected.csv (if fallback applied)

Crosswalk
---------
The crosswalk CSV is expected to live next to this script (SCRIPT_DIR) with the name:
  proposed_forest_type_crosswalk.csv

If you want a different filename/location, adjust CROSSWALK_FILENAME below (do not
introduce another "hardcoded input" unless you want to).

Dependencies
------------
- pandas
- numpy
- openpyxl (required by pandas to read .xlsx)

Exit codes
----------
0 = success
1 = failure (validation/assertion error)
"""

from __future__ import annotations

import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple, Optional, List

import numpy as np
import pandas as pd


# =============================================================================
# USER SETTINGS (edit these three values only)
# =============================================================================

# Path to the existing lookup table CSV (absolute path or relative to this script).
OLD_LOOKUP_CSV: Path = Path(r"C:\GIS\Data\LEARN\SourceData\ForestType\forest_raster_09172020.csv")

# Path to the updated harvest emission factors workbook (absolute path or relative to this script).
UPDATED_FACTORS_XLSX: Path = Path(r"C:\GIS\Data\LEARN\ForestRaster\2026\updated_harvest_emission_factors.xlsx")

# Directory where outputs (merged CSV + audit folder) will be written.
# May be absolute or relative to this script.
OUTPUT_DIR: Path = Path(r"C:\GIS\Data\LEARN\ForestRaster\2026")


# =============================================================================
# Derived paths (created automatically)
# =============================================================================

SCRIPT_DIR: Path = Path(__file__).resolve().parent
CROSSWALK_FILENAME: str = "proposed_forest_type_crosswalk.csv"

CROSSWALK_CSV: Path = SCRIPT_DIR / CROSSWALK_FILENAME
OUT_CSV: Path = OUTPUT_DIR / f"{OLD_LOOKUP_CSV.stem}_with_new_harvest_EFs.csv"
AUDIT_DIR: Path = OUTPUT_DIR / "audit_harvest_merge"

# Toggle for troubleshooting: keep helper merge fields in the output CSV.
KEEP_MERGE_FIELDS: bool = False


# =============================================================================
# Configuration / expected schemas
# =============================================================================

REGION_TO_SHEET: Dict[str, str] = {
    "Northeast": "Northeast",
    "Central States": "Northeast",
    "Northern Lake States": "Northeast",
    "Southeast": "South",
    "South Central States": "South",
    "Great Plains": "Great Pl",
    "Rocky Mountain North": "RM north",
    "Rocky Mountain South": "RM south",
    "Pacific Northwest East": "PNW E",
    "Pacific Northwest West": "PNW W",
    "Pacific Southwest": "Southwest",
}

EXPECTED_SHEETS: List[str] = [
    "Northeast",
    "South",
    "Great Pl",
    "RM north",
    "RM south",
    "PNW E",
    "PNW W",
    "Southwest",
]

AGE_CLASSES: List[str] = ["0-20", "20-100", "100+"]

WORKBOOK_SEV_COLS: List[str] = [
    "0 to 25% severity",
    "25 to 50% severity",
    "50 to 75% severity",
    "75 to 100% severity",
]

OUT_SEV_COLS: List[str] = [
    "Harvest_EF_0_25",
    "Harvest_EF_25_50",
    "Harvest_EF_50_75",
    "Harvest_EF_75_100",
]

NOTE_ROW_REGEX = re.compile(r"^\s*\(.*\)\s*$", flags=re.IGNORECASE)
PLANTED_REGEX = re.compile(r"\(planted\)\s*$", flags=re.IGNORECASE)


# =============================================================================
# Audit logging
# =============================================================================

@dataclass
class Audit:
    summary_lines: List[str] = field(default_factory=list)

    unmapped_crosswalk_rows: Optional[pd.DataFrame] = None
    missing_factor_rows: Optional[pd.DataFrame] = None
    fallback_factors_filled: Optional[pd.DataFrame] = None
    fallback_old_rows_affected: Optional[pd.DataFrame] = None

    def add(self, line: str) -> None:
        self.summary_lines.append(line)

    def write(self, audit_dir: Path) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)

        (audit_dir / "audit_summary.txt").write_text(
            "\n".join(self.summary_lines) + "\n", encoding="utf-8"
        )

        if self.unmapped_crosswalk_rows is not None:
            self.unmapped_crosswalk_rows.to_csv(
                audit_dir / "unmapped_crosswalk_rows.csv", index=False
            )
        if self.missing_factor_rows is not None:
            self.missing_factor_rows.to_csv(
                audit_dir / "missing_factor_rows.csv", index=False
            )
        if self.fallback_factors_filled is not None:
            self.fallback_factors_filled.to_csv(
                audit_dir / "fallback_factors_filled.csv", index=False
            )
        if self.fallback_old_rows_affected is not None:
            self.fallback_old_rows_affected.to_csv(
                audit_dir / "fallback_old_rows_affected.csv", index=False
            )


# =============================================================================
# Utility helpers
# =============================================================================

def resolve_path(p: Path) -> Path:
    """
    Resolve absolute/relative paths deterministically.

    - If p is absolute: return as-is
    - If p is relative: resolve relative to SCRIPT_DIR
    """
    p = Path(p)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def _require_columns(df: pd.DataFrame, required: List[str], context: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{context}: missing required columns: {missing}. Present: {list(df.columns)}")


def _blank_to_nan(series: pd.Series) -> pd.Series:
    s = series.astype("object")
    s = s.where(~s.astype(str).str.strip().isin(["", "nan", "None", "none"]), np.nan)
    return s


def detect_header_row(excel_path: Path, sheet_name: str, max_scan_rows: int = 40) -> int:
    """
    Scan the first max_scan_rows rows looking for a row containing both 'Forest Type' and 'Age class'.
    Returns the 0-based row index to use as pandas header=...
    """
    raw = pd.read_excel(excel_path, sheet_name=sheet_name, header=None, nrows=max_scan_rows)
    for i, row in raw.iterrows():
        vals = [str(v).strip() if pd.notna(v) else "" for v in row.values.tolist()]
        if "Forest Type" in vals and "Age class" in vals:
            return int(i)
    raise ValueError(f"Could not detect header row in sheet '{sheet_name}' within first {max_scan_rows} rows.")


# =============================================================================
# Load + tidy new workbook factors
# =============================================================================

def load_harvest_factors(excel_path: Path, audit: Audit) -> pd.DataFrame:
    xl = pd.ExcelFile(excel_path)
    found = xl.sheet_names

    missing_expected = [s for s in EXPECTED_SHEETS if s not in found]
    if missing_expected:
        raise ValueError(f"Workbook missing expected sheets: {missing_expected}. Found: {found}")

    frames: List[pd.DataFrame] = []

    for sheet in EXPECTED_SHEETS:
        header_row = detect_header_row(excel_path, sheet_name=sheet)
        df = pd.read_excel(excel_path, sheet_name=sheet, header=header_row)

        _require_columns(df, ["Forest Type", "Age class"] + WORKBOOK_SEV_COLS, context=f"Workbook sheet '{sheet}'")

        df = df.dropna(how="all").copy()

        # Remove parenthetical note rows and forward-fill the forest type
        note_mask = df["Forest Type"].astype(str).str.match(NOTE_ROW_REGEX)
        df.loc[note_mask, "Forest Type"] = np.nan
        df["Forest Type"] = df["Forest Type"].ffill()
        df["Forest Type"] = df["Forest Type"].astype(str).str.strip()

        df["Age class"] = df["Age class"].astype(str).str.strip()
        df = df[df["Age class"].isin(AGE_CLASSES)].copy()

        for c in WORKBOOK_SEV_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df["sheet"] = sheet
        frames.append(df[["sheet", "Forest Type", "Age class"] + WORKBOOK_SEV_COLS])

    factors = pd.concat(frames, ignore_index=True)

    # Keys must be unique for a deterministic many-to-one merge
    dup = factors.duplicated(subset=["sheet", "Forest Type", "Age class"], keep=False)
    if dup.any():
        ex = factors.loc[dup].sort_values(["sheet", "Forest Type", "Age class"]).head(30)
        raise ValueError(
            "Workbook contains duplicate factor keys (sheet, Forest Type, Age class). "
            "These must be unique. Example:\n"
            f"{ex.to_string(index=False)}"
        )

    # Deterministic fallback: fill planted rows with missing severities from the corresponding non-planted type.
    missing_mask = factors[WORKBOOK_SEV_COLS].isna().any(axis=1)
    planted_mask = factors["Forest Type"].astype(str).str.contains(PLANTED_REGEX)
    needs_fill = factors.loc[missing_mask & planted_mask].copy()

    filled_log: List[dict] = []
    if not needs_fill.empty:
        for idx, row in needs_fill.iterrows():
            planted_type = str(row["Forest Type"]).strip()
            base_type = PLANTED_REGEX.sub("", planted_type).strip()
            lookup = factors[
                (factors["sheet"] == row["sheet"])
                & (factors["Forest Type"] == base_type)
                & (factors["Age class"] == row["Age class"])
            ]
            if lookup.shape[0] != 1:
                raise ValueError(
                    "Fallback failed: expected exactly one non-planted counterpart for "
                    f"'{planted_type}' (sheet={row['sheet']}, age={row['Age class']}), found {lookup.shape[0]}."
                )
            for c in WORKBOOK_SEV_COLS:
                factors.at[idx, c] = float(lookup.iloc[0][c])

            filled_log.append(
                {
                    "sheet": row["sheet"],
                    "Forest Type (planted)": planted_type,
                    "Forest Type (base)": base_type,
                    "Age class": row["Age class"],
                }
            )

    if filled_log:
        audit.fallback_factors_filled = pd.DataFrame(filled_log)

    # After fallback, factors must be fully populated
    if factors[WORKBOOK_SEV_COLS].isna().any(axis=None):
        ex = factors.loc[factors[WORKBOOK_SEV_COLS].isna().any(axis=1)].head(30)
        raise ValueError(
            "Workbook still has missing severity values after fallback. Example:\n"
            f"{ex.to_string(index=False)}"
        )

    audit.add(f"Workbook factors loaded: {len(factors):,} rows")
    audit.add(
        "Workbook factor keys (sheet, Forest Type, Age class): "
        f"{factors[['sheet','Forest Type','Age class']].drop_duplicates().shape[0]:,}"
    )
    audit.add(
        "Workbook fallback fills applied: "
        f"{0 if audit.fallback_factors_filled is None else len(audit.fallback_factors_filled):,} factor rows"
    )

    return factors


# =============================================================================
# Load crosswalk and build resolver
# =============================================================================

def load_crosswalk(crosswalk_csv: Path, audit: Audit) -> pd.DataFrame:
    cw = pd.read_csv(crosswalk_csv)
    cw.columns = [str(c).strip() for c in cw.columns]

    _require_columns(cw, ["sheet", "KeyGroup", "NewForestType"], context="Crosswalk CSV")

    cw = cw[["sheet", "KeyGroup", "NewForestType"]].copy()
    cw["sheet"] = cw["sheet"].astype(str).str.strip()
    cw["KeyGroup"] = _blank_to_nan(cw["KeyGroup"].astype(str).str.strip())
    cw["NewForestType"] = cw["NewForestType"].astype(str).str.strip()

    bad_sheets = sorted(set(cw["sheet"].dropna().unique()) - set(EXPECTED_SHEETS))
    if bad_sheets:
        raise ValueError(f"Crosswalk contains unexpected sheet values: {bad_sheets}. Expected: {EXPECTED_SHEETS}")

    audit.add(f"Crosswalk loaded: {len(cw):,} rows")
    audit.add(f"Crosswalk distinct (sheet, KeyGroup): {cw[['sheet','KeyGroup']].drop_duplicates().shape[0]:,}")
    return cw


def build_crosswalk_resolver(cw: pd.DataFrame) -> Dict[Tuple[str, str], Dict[str, str]]:
    """
    Build an in-memory resolver for deterministic mapping.

    Output:
      (sheet, KeyGroup) -> {"default": NewForestType}
    or
      (sheet, KeyGroup) -> {"planted": ..., "non_planted": ...}
    """
    resolver: Dict[Tuple[str, str], Dict[str, str]] = {}

    for (sheet, keygroup), g in cw.groupby(["sheet", "KeyGroup"], dropna=False):
        if pd.isna(sheet) or pd.isna(keygroup):
            continue

        types = list(pd.unique(g["NewForestType"]))
        if len(types) == 1:
            resolver[(sheet, keygroup)] = {"default": types[0]}
            continue

        if len(types) == 2:
            planted = [t for t in types if PLANTED_REGEX.search(t)]
            non_planted = [t for t in types if not PLANTED_REGEX.search(t)]
            if len(planted) == 1 and len(non_planted) == 1:
                resolver[(sheet, keygroup)] = {"planted": planted[0], "non_planted": non_planted[0]}
                continue

        raise ValueError(
            "Ambiguous crosswalk mappings for "
            f"(sheet={sheet!r}, KeyGroup={keygroup!r}). "
            "Expected 1 mapping or exactly 2 mappings with one '(planted)'. "
            f"Found: {types}"
        )

    return resolver


# =============================================================================
# Load old lookup table and merge
# =============================================================================

def prepare_old_table(old_csv: Path, audit: Audit) -> pd.DataFrame:
    old = pd.read_csv(old_csv)

    required = [
        "ForestAgeTypeRegion",
        "Region",
        "Age",
        "plant_con_",
        "UpperGroup",
        "Plantation",
        "Harvest Emissions Factor",
    ]
    _require_columns(old, required, context="Old CSV")

    if old["ForestAgeTypeRegion"].duplicated().any():
        ex = old.loc[old["ForestAgeTypeRegion"].duplicated(), "ForestAgeTypeRegion"].head(20).tolist()
        raise ValueError(f"Old CSV must have unique ForestAgeTypeRegion. Example duplicates: {ex}")

    old = old.copy()

    # Region -> workbook sheet
    old["sheet"] = old["Region"].map(REGION_TO_SHEET)
    if old["sheet"].isna().any():
        bad_regions = sorted(old.loc[old["sheet"].isna(), "Region"].unique().tolist())
        raise ValueError(f"Old CSV contains Region values not covered by REGION_TO_SHEET: {bad_regions}")

    # Standardize Age
    old["Age"] = old["Age"].astype(str).str.strip()
    bad_ages = sorted(set(old["Age"].unique()) - set(AGE_CLASSES))
    if bad_ages:
        raise ValueError(f"Old CSV contains unexpected Age values: {bad_ages}. Expected: {AGE_CLASSES}")

    # Compute plantation flag
    pc = pd.to_numeric(old["plant_con_"], errors="coerce").fillna(0).astype(int)
    old["is_plantation_row"] = pc.gt(0)

    # KeyGroup selection rule
    old["UpperGroup"] = _blank_to_nan(old["UpperGroup"])
    old["Plantation"] = _blank_to_nan(old["Plantation"])
    old["KeyGroup"] = np.where(old["is_plantation_row"], old["Plantation"], old["UpperGroup"])
    old["KeyGroup"] = _blank_to_nan(old["KeyGroup"])

    audit.add(f"Old lookup loaded: {len(old):,} rows")
    audit.add(f"Old distinct (sheet, KeyGroup): {old[['sheet','KeyGroup']].drop_duplicates().shape[0]:,}")

    return old


def merge_factors(
    old: pd.DataFrame,
    factors: pd.DataFrame,
    resolver: Dict[Tuple[str, str], Dict[str, str]],
    audit: Audit,
) -> pd.DataFrame:
    old = old.copy()

    # Resolve NewForestType deterministically
    def _resolve(row) -> Optional[str]:
        kg = row["KeyGroup"]
        if pd.isna(kg):
            return None
        key = (row["sheet"], kg)
        entry = resolver.get(key)
        if entry is None:
            return None
        if "default" in entry:
            return entry["default"]
        return entry["planted"] if bool(row["is_plantation_row"]) else entry["non_planted"]

    old["NewForestType"] = old.apply(_resolve, axis=1)

    unmapped = old[old["NewForestType"].isna()].copy()
    if not unmapped.empty:
        # Allow ONLY blank-group + zero-harvest-factor rows (explicit placeholders in the old table)
        allowed = (
            unmapped["KeyGroup"].isna()
            & unmapped["UpperGroup"].isna()
            & unmapped["Plantation"].isna()
            & (pd.to_numeric(unmapped["Harvest Emissions Factor"], errors="coerce").fillna(0.0) == 0.0)
        )
        bad = unmapped[~allowed]
        # Always write the unmapped set to audit (good transparency even when allowed)
        audit.unmapped_crosswalk_rows = unmapped[
            [
                "ForestAgeTypeRegion",
                "Region",
                "sheet",
                "Age",
                "plant_con_",
                "UpperGroup",
                "Plantation",
                "KeyGroup",
                "Harvest Emissions Factor",
            ]
        ].sort_values(["sheet", "Region", "Age"])

        if not bad.empty:
            raise ValueError(
                "Crosswalk did not map some old rows, and they are not allowable blank/zero rows. "
                "See audit/unmapped_crosswalk_rows.csv."
            )

    # Prepare factors for merge
    factors_m = factors.copy().rename(columns=dict(zip(WORKBOOK_SEV_COLS, OUT_SEV_COLS)))

    merged = old.merge(
        factors_m,
        how="left",
        left_on=["sheet", "NewForestType", "Age"],
        right_on=["sheet", "Forest Type", "Age class"],
        validate="m:1",
    )

    # Missing factor matches:
    missing_mask = merged[OUT_SEV_COLS].isna().any(axis=1)
    if missing_mask.any():
        missing_rows = merged.loc[missing_mask].copy()

        # Allowable blank rows -> zeros
        allowable_blank = (
            missing_rows["NewForestType"].isna()
            & missing_rows["KeyGroup"].isna()
            & missing_rows["UpperGroup"].isna()
            & missing_rows["Plantation"].isna()
            & (pd.to_numeric(missing_rows["Harvest Emissions Factor"], errors="coerce").fillna(0.0) == 0.0)
        )
        merged.loc[missing_mask & allowable_blank, OUT_SEV_COLS] = 0.0

        # Remaining missing rows are errors
        still_missing = merged[OUT_SEV_COLS].isna().any(axis=1)
        if still_missing.any():
            audit.missing_factor_rows = merged.loc[still_missing, [
                "ForestAgeTypeRegion",
                "Region",
                "sheet",
                "Age",
                "plant_con_",
                "KeyGroup",
                "NewForestType",
                "UpperGroup",
                "Plantation",
            ] + OUT_SEV_COLS].sort_values(["sheet", "Region", "NewForestType", "Age"])
            raise ValueError(
                "Some rows could not be matched to workbook factors. See audit/missing_factor_rows.csv."
            )

    # Link fallback-filled workbook rows back to old rows for transparency
    if audit.fallback_factors_filled is not None and not audit.fallback_factors_filled.empty:
        aff = audit.fallback_factors_filled.rename(
            columns={"Forest Type (planted)": "NewForestType", "Age class": "Age"}
        )
        affected = merged.merge(aff[["sheet", "NewForestType", "Age"]], how="inner", on=["sheet", "NewForestType", "Age"])
        audit.fallback_old_rows_affected = affected[
            ["ForestAgeTypeRegion", "Region", "sheet", "Age", "plant_con_", "KeyGroup", "NewForestType"] + OUT_SEV_COLS
        ].sort_values(["sheet", "Region", "NewForestType", "Age"])

    # Final invariants
    if len(merged) != len(old):
        raise ValueError(f"Row count changed after merge: old={len(old):,}, merged={len(merged):,}.")

    if merged[OUT_SEV_COLS].isna().any(axis=None):
        raise ValueError("Internal error: NaNs remain in new severity columns after all handling.")

    return merged


# =============================================================================
# Entry point
# =============================================================================

def run() -> int:
    audit = Audit()

    old_csv = resolve_path(OLD_LOOKUP_CSV)
    factors_xlsx = resolve_path(UPDATED_FACTORS_XLSX)
    out_dir = resolve_path(OUTPUT_DIR)
    out_csv = (out_dir / OUT_CSV.name)  # keep auto filename, but relative to resolved output dir
    audit_dir = (out_dir / AUDIT_DIR.name)
    crosswalk_csv = resolve_path(CROSSWALK_CSV)

    # Preflight: show config in audit summary (human-readable)
    audit.add("Configuration")
    audit.add("-------------")
    audit.add(f"Script directory: {SCRIPT_DIR}")
    audit.add(f"Old lookup CSV:   {old_csv}")
    audit.add(f"Factors XLSX:     {factors_xlsx}")
    audit.add(f"Crosswalk CSV:    {crosswalk_csv}")
    audit.add(f"Output directory: {out_dir}")
    audit.add(f"Output CSV:       {out_csv}")
    audit.add(f"Audit directory:  {audit_dir}")
    audit.add(f"KEEP_MERGE_FIELDS: {KEEP_MERGE_FIELDS}")
    audit.add("")

    try:
        if not old_csv.exists():
            raise FileNotFoundError(f"Old lookup CSV not found: {old_csv}")
        if not factors_xlsx.exists():
            raise FileNotFoundError(f"Factors XLSX not found: {factors_xlsx}")
        if not crosswalk_csv.exists():
            raise FileNotFoundError(
                f"Crosswalk CSV not found: {crosswalk_csv} (expected next to script as '{CROSSWALK_FILENAME}')"
            )

        out_dir.mkdir(parents=True, exist_ok=True)

        old = prepare_old_table(old_csv, audit=audit)
        factors = load_harvest_factors(factors_xlsx, audit=audit)
        cw = load_crosswalk(crosswalk_csv, audit=audit)
        resolver = build_crosswalk_resolver(cw)

        merged = merge_factors(old, factors, resolver, audit=audit)

        # Coverage stats
        total_rows = len(old)
        mapped_rows = int(merged["NewForestType"].notna().sum())
        blank_rows = total_rows - mapped_rows
        fallback_factor_rows = 0 if audit.fallback_factors_filled is None else len(audit.fallback_factors_filled)

        audit.add("")
        audit.add("Coverage / stats")
        audit.add("----------------")
        audit.add(f"Total old rows: {total_rows:,}")
        audit.add(f"Rows mapped to workbook forest type: {mapped_rows:,}")
        audit.add(f"Allowable blank/zero rows (no forest type): {blank_rows:,}")
        audit.add(f"Workbook factor rows filled by fallback: {fallback_factor_rows:,}")

        # Optional: drop helper columns from final output
        helper_cols = ["sheet", "KeyGroup", "NewForestType", "is_plantation_row", "Forest Type", "Age class"]
        if not KEEP_MERGE_FIELDS:
            merged = merged.drop(columns=[c for c in helper_cols if c in merged.columns], errors="ignore")

        # Ensure output columns exist
        for c in OUT_SEV_COLS:
            if c not in merged.columns:
                raise ValueError(f"Expected output column missing: {c}")

        merged.to_csv(out_csv, index=False)

        audit.add("")
        audit.add(f"Output written: {out_csv}")
        audit.write(audit_dir)
        audit.add(f"Audit written:  {audit_dir}")
        audit.write(audit_dir)  # rewrite summary with final line

        print("\n".join(audit.summary_lines))
        return 0

    except Exception as e:
        # Best-effort audit write on error
        audit.add("")
        audit.add("ERROR")
        audit.add("-----")
        audit.add(str(e))
        try:
            audit.write(audit_dir)
        except Exception:
            pass
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
