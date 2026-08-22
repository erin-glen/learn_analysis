"""Build a user-friendly FRF summary: net flux + area by GAP-2.5 protection
class, per inventory period (recat_false), from the long
frf_learn_by_protection_gap25.csv produced by the roadless run.

Writes a standalone FRF_summary.xlsx AND an augmented copy of
FRF_LEARN_summaries.xlsx (with the "Summary" sheet inserted after README)
into a staging folder. The canonical FRF_LEARN_summaries.xlsx is read only.
"""
from pathlib import Path
import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

SRC = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs\summaries_FRF")
STAGE = Path(r"C:\Users\Erin.Glen\Downloads\_frf_stage")
STANDALONE = STAGE / "FRF_summary.xlsx"
BIG = SRC / "FRF_LEARN_summaries.xlsx"            # canonical source, read-only
BIG_OUT = STAGE / "FRF_LEARN_summaries.xlsx"      # augmented copy for the zip

AREA = "Area (ha, total)"
REM = "Removals (t CO2e/yr)"
EMIS = "Emissions (t CO2e/yr)"

PERIODS = ["2013_2016", "2016_2019", "2019_2021", "2021_2023"]
PERIOD_LBL = {p: p.replace("_", "–") for p in PERIODS}

CLASS_LBL = {
    "1": "GAP 1 – Permanent protection",
    "2": "GAP 2 – Permanent, managed",
    "2.5": "GAP 2.5 – Roadless (GAP 3 + roadless)",
    "3": "GAP 3 – Multiple use",
    "4": "GAP 4 – No known mandate",
}
CLASS_ORDER = ["1", "2", "2.5", "3", "4"]
METRICS = [
    ("Net flux (Mt CO₂e/yr)", "net"),
    ("  of which removals", "rem"),
    ("  of which emissions", "emis"),
    ("Forest area (Mha)", "area"),
]

NAVY = PatternFill("solid", fgColor="1F3864")
BLUE = PatternFill("solid", fgColor="D9E1F2")
GREY = PatternFill("solid", fgColor="F2F2F2")
WHITE_BOLD = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)
ITALIC = Font(italic=True, size=9, color="595959")
_thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def load_grouped():
    df = pd.read_csv(SRC / "frf_learn_by_protection_gap25.csv")
    df = df[df["recat_mode"] == "recat_false"].copy()
    df["protection"] = df["protection"].apply(lambda x: "%g" % float(x))
    for c in (AREA, REM, EMIS):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    g = df.groupby(["protection", "inventory_period"]).agg(
        area=(AREA, "sum"), rem=(REM, "sum"), emis=(EMIS, "sum")
    ).reset_index()
    g["net"] = g["rem"] + g["emis"]
    return g


def draw_summary(ws, g):
    def val(cls, period, metric):
        row = g[(g["protection"] == cls) & (g["inventory_period"] == period)]
        if row.empty:
            return None
        if metric == "net":
            # keep net exactly additive with the displayed rounded components
            rem = round(float(row.iloc[0]["rem"]) / 1e6, 3)
            emis = round(float(row.iloc[0]["emis"]) / 1e6, 3)
            return round(rem + emis, 3)
        return round(float(row.iloc[0][metric]) / 1e6, 3)  # t->Mt, ha->Mha

    ncols = 2 + len(PERIODS)
    last_col = chr(ord("A") + ncols - 1)

    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = ("USFS / BLM Forest Reference Levels — Net Flux by Protection "
                "Class (GAP 2.5, roadless distinguished)")
    ws["A1"].font = Font(bold=True, size=12)
    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = ("recat_false (recommended mode)  ·  negative = net sink, "
                "positive = net source  ·  totals are CONUS USFS + BLM forest")
    ws["A2"].font = ITALIC

    hdr = 4
    ws.cell(hdr, 1, "Protection class").font = WHITE_BOLD
    ws.cell(hdr, 2, "Metric").font = WHITE_BOLD
    for j, p in enumerate(PERIODS):
        c = ws.cell(hdr, 3 + j, PERIOD_LBL[p])
        c.font = WHITE_BOLD
        c.alignment = Alignment(horizontal="center")
    for col in range(1, ncols + 1):
        ws.cell(hdr, col).fill = NAVY
        ws.cell(hdr, col).border = BORDER

    r = hdr + 1
    for ci, cls in enumerate(CLASS_ORDER):
        block_top = r
        band = BLUE if ci % 2 == 0 else GREY
        for mlabel, mkey in METRICS:
            ws.cell(r, 2, mlabel)
            if mkey == "net":
                ws.cell(r, 2).font = BOLD
            for j, p in enumerate(PERIODS):
                cell = ws.cell(r, 3 + j, val(cls, p, mkey))
                cell.number_format = "#,##0.00;(#,##0.00)"
                cell.alignment = Alignment(horizontal="right")
                if mkey == "net":
                    cell.font = BOLD
            for col in range(1, ncols + 1):
                ws.cell(r, col).fill = band
                ws.cell(r, col).border = BORDER
            r += 1
        ws.merge_cells(start_row=block_top, start_column=1, end_row=r - 1, end_column=1)
        lc = ws.cell(block_top, 1, CLASS_LBL[cls])
        lc.font = BOLD
        lc.alignment = Alignment(vertical="center", wrap_text=True)

    r += 1
    notes = [
        "GAP 2.5 = USFS/BLM lands where 2001 Roadless Rule areas (EDW S_USA.RoadlessArea_2001_ID_CO) overlay GAP 3; "
        "roadless never demotes GAP 1/2. In this dataset all roadless falls on GAP 3, so GAP 2.5 = the roadless area.",
        "GAP 2.5 / roadless is a net sink in 2013–16, 2016–19 and 2021–23 but flips to a net SOURCE in 2019–2021 "
        "(+3.9 Mt CO₂e/yr), driven by a fire-emissions spike that period (~31 Mt vs ~20 Mt in other periods).",
        "Net flux = removals (sink, negative) + emissions (source, positive). recat_false is the recommended mode; a "
        "recat_true copy of the underlying tables is also in the package.",
    ]
    for n in notes:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
        c = ws.cell(r, 1, "• " + n)
        c.font = ITALIC
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 28
        r += 1

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22
    for j in range(len(PERIODS)):
        ws.column_dimensions[chr(ord("C") + j)].width = 13
    ws.freeze_panes = "C5"


def main():
    STAGE.mkdir(parents=True, exist_ok=True)
    g = load_grouped()

    # Standalone workbook
    wb = Workbook()
    draw_summary(wb.active, g)
    wb.active.title = "Summary"
    wb.save(STANDALONE)
    print("wrote", STANDALONE)

    # Augmented copy of the big workbook: Summary inserted as first data tab
    wb2 = load_workbook(BIG)
    if "Summary" in wb2.sheetnames:
        del wb2["Summary"]
    ws = wb2.create_sheet("Summary")
    draw_summary(ws, g)
    pos = 1 if (wb2.sheetnames and wb2.sheetnames[0] == "README") else 0
    wb2.move_sheet("Summary", offset=-(wb2.sheetnames.index("Summary") - pos))
    wb2.active = pos
    wb2.save(BIG_OUT)
    print("wrote", BIG_OUT, "->", wb2.sheetnames)


if __name__ == "__main__":
    main()
