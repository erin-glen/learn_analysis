"""
Bundle the LEARN-format FRF summaries into a single, formatted Excel workbook for
non-technical recipients.

Reads the `frf_learn_*.csv` files from summaries_FRF/ and writes
`FRF_LEARN_summaries.xlsx` alongside them.

Layout:
    README                    overview + key, units, recat_mode notes
    National Totals           mode x period x LEARN_Type (56 rows)
    By Agency                 USFS vs BLM
    By GAP Protection         GAP status 1-4
    By FS Region              R1-R10 + BLM
    By State                  50 states + PR
    By Unit                   per-PAD-US-unit (large sheet)

Formatting:
    - Header row: bold, navy fill, white text, frozen
    - AutoFilter on every data sheet
    - Numeric formats: integer w/ thousand separators for area & flux; 2 dp for factor
    - Removals colored blue, Emissions colored red (mild tints)
    - Sensible column widths
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

import compile_padus_results as compile_mod

BASE = compile_mod.BASE_DIR
SUM = BASE / "summaries_FRF"
OUT = SUM / "FRF_LEARN_summaries.xlsx"

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
REMOVAL_FILL = PatternFill("solid", fgColor="E3EEF7")
EMISSION_FILL = PatternFill("solid", fgColor="FBE9E7")
THIN_BORDER = Border(bottom=Side(style="thin", color="B0B0B0"))

# Column labels presented to readers (display only; data columns keep their CSV names)
DISPLAY_NAMES = {
    "recat_mode":      "Recat Mode",
    "inventory_period":"Inventory Period",
    "Geography_ID":    "Geography ID",
    "mang_name":       "Agency",
    "unit_nm":         "Unit Name",
    "state_nm":        "State",
    "gap_sts":         "GAP Status",
    "acres_nat":       "Unit Acres (PAD-US)",
    "fs_region":       "FS Region",
    "LEARN_Type":      "LEARN Type",
}

SHEETS = [
    ("National Totals",     "frf_learn_totals.csv",         []),
    ("By Agency",           "frf_learn_by_agency.csv",      ["mang_name"]),
    ("By GAP Protection",   "frf_learn_by_protection.csv",  ["gap_sts"]),
    ("By FS Region",        "frf_learn_by_fs_region.csv",   ["fs_region"]),
    ("By State",            "frf_learn_by_state.csv",       ["state_nm"]),
    ("By Unit",             "frf_learn_by_unit.csv",
     ["Geography_ID", "mang_name", "unit_nm", "state_nm",
      "gap_sts", "acres_nat", "fs_region"]),
]

DATA_COLS = [
    "LEARN_Type",
    "Area (ha, total)",
    "Removals (t CO2e/yr)",
    "Emissions (t CO2e/yr)",
    "Factor (t C/ha for emissions, t C/ha/yr for removals)",
]


def write_readme(ws):
    rows = [
        ("Forest Remaining Forest (FRF) — LEARN GHG Inventory Summaries", True),
        ("", False),
        ("Source: LEARN national PAD-US unit-level forest runs, 2 recategorization modes x 4 inventory periods (2013–2016, 2016–2019, 2019–2021, 2021–2023).", False),
        ("Coverage: All USFS and BLM management units in the conterminous US (605 units).", False),
        ("", False),
        ("How to read each data sheet", True),
        ("Each (Recat Mode, Inventory Period, area-of-interest) combination has 7 rows — one per LEARN category. Use Excel's filter buttons on the header row to drill in.", False),
        ("", False),
        ("Columns", True),
        ("Recat Mode — recat_false uses the standard LEARN disturbance hierarchy and is recommended for inter-period comparisons. recat_true applies an alternate fire-priority pixel rule (Fire area is much higher; the other categories are similar).", False),
        ("Inventory Period — three- or two-year window the rate of disturbance is averaged over (2013–2016 and 2016–2019 are 3-yr; 2019–2021 and 2021–2023 are 2-yr).", False),
        ("LEARN Type — one of: Undisturbed Forest; Disturbed Forest: Fire / Insect/Disease; Harvest: Low / Low Moderate / High Moderate / High. Harvest classes correspond to NLCD canopy-loss intensity bins 0–25 / 25–50 / 50–75 / 75–100 %.", False),
        ("Area (ha, total) — total Forest-Remaining-Forest area in that LEARN category for the AOI.", False),
        ("Removals (t CO2e/yr) — annualized net carbon removal (sink, negative number). Populated only for Undisturbed Forest.", False),
        ("Emissions (t CO2e/yr) — annualized net carbon emission. Populated only for the six disturbance categories. Insect/Disease can be negative for some AOIs (this is intentional in the LEARN model: low-intensity insect damage in some forest-age-region combos leaves enough live biomass to net-uptake C).", False),
        ("Factor (t C/ha for emissions, t C/ha/yr for removals) — area-weighted average emission/removal factor reconstructed from the AOI totals. Units: t C/ha one-time per disturbed hectare for the six disturbance categories; t C/ha/yr for Undisturbed Forest removals. Negative values in Harvest: Low / Low Moderate / High Moderate are also intentional in the LEARN model (see below).", False),
        ("", False),
        ("Sign convention", True),
        ("Negative = atmospheric sink (carbon goes into the forest). Positive = atmospheric source (carbon released).", False),
        ("", False),
        ("Conversion", True),
        ("Carbon and CO2 are related by molecular weight: 1 t C = 44/12 ≈ 3.667 t CO2e. The Factor column reports tonnes of carbon (t C) per the LEARN convention; Removals and Emissions report tonnes of CO2-equivalent (t CO2e) per year.", False),
        ("", False),
        ("Known model behaviors that look surprising", True),
        ("- ~42 % of Insect/Disease emission factors in the LEARN lookup are negative by design.", False),
        ("- 5–21 % of Harvest emission factors are also negative by design, concentrated in low-intensity classes; \"Harvest: High\" is never negative.", False),
        ("- A handful of tiny BLM/USFS units (1–2 ha of forest in arid SW states) show Undisturbed Forest with area>0 but flux=0, because the underlying pixel(s) fall in a forest-age-region cell whose removal factor is 0 in the LEARN lookup.", False),
        ("", False),
        ("Sheets in this workbook", True),
        ("National Totals — 56 rows (2 modes x 4 periods x 7 LEARN types).", False),
        ("By Agency — USFS vs BLM rollup.", False),
        ("By GAP Protection — GAP status 1–4.", False),
        ("By FS Region — Forest Service Region 1–10 (BLM lumped as \"BLM\").", False),
        ("By State — state rollup.", False),
        ("By Unit — per-PAD-US-unit (605 units, ~34k rows).", False),
    ]
    ws.column_dimensions["A"].width = 150
    for i, (text, is_header) in enumerate(rows, start=1):
        cell = ws.cell(row=i, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if is_header:
            cell.font = Font(bold=True, size=12, color="1F3A5F")
        ws.row_dimensions[i].height = 18 if is_header else (None if not text else 30)
    ws.sheet_view.showGridLines = False


def _column_width(values, header):
    raw = [str(header)] + [("" if pd.isna(v) else str(v)) for v in values[:200]]
    return min(max(len(s) for s in raw) + 2, 50)


def write_data_sheet(ws, df: pd.DataFrame, dim_cols: list[str]):
    """Write `df` to `ws` with header styling, autofilter, freeze, number formats."""
    df = df.copy()

    # Order: mode, period, [dim cols], LEARN_Type, Area, Removals, Emissions, Factor
    ordered = ["recat_mode", "inventory_period"] + dim_cols + DATA_COLS
    df = df[ordered]

    # Sort for readability (mode, period, [dim], LEARN_Type already in LEARN order)
    if dim_cols:
        df = df.sort_values(["recat_mode", "inventory_period"] + dim_cols)
    else:
        df = df.sort_values(["recat_mode", "inventory_period"])

    headers = [DISPLAY_NAMES.get(c, c) for c in df.columns]

    # Header row
    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=j, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 36

    # Data rows
    col_idx = {c: j + 1 for j, c in enumerate(df.columns)}
    n_rows = len(df)
    for i, row in enumerate(df.itertuples(index=False), start=2):
        for j, col in enumerate(df.columns, start=1):
            val = row[j - 1]
            if pd.isna(val):
                ws.cell(row=i, column=j, value=None)
                continue
            cell = ws.cell(row=i, column=j, value=val)
            if col == "Area (ha, total)":
                cell.number_format = '#,##0'
            elif col in ("Removals (t CO2e/yr)", "Emissions (t CO2e/yr)"):
                cell.number_format = '#,##0'
                cell.fill = REMOVAL_FILL if col == "Removals (t CO2e/yr)" else EMISSION_FILL
            elif col == "Factor (t C/ha for emissions, t C/ha/yr for removals)":
                cell.number_format = '0.00'
            elif col == "acres_nat":
                cell.number_format = '#,##0'

    # Column widths
    for j, col in enumerate(df.columns, start=1):
        letter = get_column_letter(j)
        if col == "Factor (t C/ha for emissions, t C/ha/yr for removals)":
            ws.column_dimensions[letter].width = 18
        elif col in ("Removals (t CO2e/yr)", "Emissions (t CO2e/yr)", "Area (ha, total)"):
            ws.column_dimensions[letter].width = 16
        elif col == "LEARN_Type":
            ws.column_dimensions[letter].width = 36
        elif col == "unit_nm":
            ws.column_dimensions[letter].width = 36
        elif col == "Geography_ID":
            ws.column_dimensions[letter].width = 14
        else:
            ws.column_dimensions[letter].width = _column_width(df[col].tolist(), DISPLAY_NAMES.get(col, col))

    # Wrap factor column header (it's long) - already wrapped via header alignment
    # Freeze header and (mode, period) columns for navigation
    ws.freeze_panes = ws.cell(row=2, column=3)

    # AutoFilter over data range
    last_col = get_column_letter(len(df.columns))
    last_row = n_rows + 1
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"


def main():
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    write_readme(readme)

    for sheet_name, csv_name, dim_cols in SHEETS:
        ws = wb.create_sheet(sheet_name)
        df = pd.read_csv(SUM / csv_name)
        # Restrict dim_cols to those actually present
        dim_cols = [c for c in dim_cols if c in df.columns]
        write_data_sheet(ws, df, dim_cols)
        print(f"  wrote sheet {sheet_name!r} ({len(df):,} rows)")

    wb.save(OUT)
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
