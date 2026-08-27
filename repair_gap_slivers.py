"""
Repair the 220 fragmented polygons in the GAP run without re-running the batch.

The problem
-----------
`prepare_padus_gap_aoi.py` carved fee polygons by wilderness designation and then by
roadless extent. Where both boundaries are complex the intersection produced
pathologically fragmented parts -- e.g. Gunnison NF GAP 1 roadless is 56 ha spread
over 1,701 pieces averaging 0.03 ha, a third of a 30 m pixel, inside an 11,869 km2
bounding box it fills 0.00% of. On geometry that fragmented,
`arcpy.management.Clip(..., "ClippingGeometry")` stops honouring the polygon and
falls back to the bounding box, so the analysis measures the whole parent forest.
220 of 1,747 polygons are affected; they inflate national FRF area by 1.48 Mha and
GAP 1 specifically by 15.1%.

The repair
----------
Every affected polygon is one part of a parent group that the earlier roadless run
measured correctly, and the roadless run is a valid baseline: on the 19,796 group
rows containing no fragmented part, the two runs agree on area to 0.027%.

So for each (mang_name, unit_nm, state_nm, roadless, period, Type) group:

    residual = roadless_run_value - sum(uncontaminated GAP parts)

and the residual is assigned to the contaminated part(s). Where a group has exactly
one contaminated part -- 656 of 780 groups -- this is exact arithmetic, not an
estimate. Where it has several, the residual is split between them in proportion to
their AOI land area, which is the only assumption in the method and covers 16% of
groups.

Area and flux are repaired the same way. Negative residuals (which would mean the
good parts already exceed the parent total) are clamped to zero and reported.

Writes combined_results_PADUS_GAP_REPAIRED_ALL.csv, which downstream summarize/
workbook steps can consume in place of the raw compiled CSV.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyogrio

BASE = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs")
RAW = BASE / "combined_results_PADUS_GAP_ALL.csv"
REF = BASE / "combined_results_PADUS_ROADLESS_ALL.csv"
OUT = BASE / "combined_results_PADUS_GAP_REPAIRED_ALL.csv"
AOI = Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_gap_roadless.shp")

AREA = "Area (ha, total)"
FLUX = "GHG Flux (t CO2e/year)"
GROUP = ["mang_name", "unit_nm", "state_nm", "roadless", "inventory_period", "Type"]
CONTAMINATION_TOLERANCE = 1.02  # FRF area > 102% of the polygon's own land area


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df = df[(df["recat_mode"] == "recat_true") & (df["Category"] == "Forest Remaining Forest")].copy()
    for c in (AREA, FLUX):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def main() -> None:
    aoi = pyogrio.read_dataframe(AOI, read_geometry=False)
    aoi["land_ha"] = aoi["acres_nat"] * 0.404686
    land = aoi.set_index("unit_id")["land_ha"]

    gap = load(RAW)
    ref = load(REF)

    # A polygon is contaminated if, in any period, its FRF area exceeds its own land area.
    per_poly = gap.groupby(["Geography_ID", "inventory_period"])[AREA].sum().reset_index()
    per_poly["land_ha"] = per_poly["Geography_ID"].map(land)
    bad_ids = set(per_poly.loc[per_poly[AREA] > per_poly["land_ha"] * CONTAMINATION_TOLERANCE,
                               "Geography_ID"])
    gap["bad"] = gap["Geography_ID"].isin(bad_ids)
    print(f"contaminated polygons: {len(bad_ids)} of {gap['Geography_ID'].nunique()}")
    print(f"contaminated rows:     {int(gap['bad'].sum()):,} of {len(gap):,}")

    # Parent-group totals from the validated roadless run, and the clean part of the GAP run.
    ref_tot = ref.groupby(GROUP)[[AREA, FLUX]].sum()
    good_tot = gap[~gap["bad"]].groupby(GROUP)[[AREA, FLUX]].sum()

    residual = ref_tot.subtract(good_tot, fill_value=0.0)
    residual = residual.rename(columns={AREA: "res_area", FLUX: "res_flux"})

    bad = gap[gap["bad"]].copy()
    bad["land_ha"] = bad["Geography_ID"].map(land).fillna(0.0)
    bad = bad.join(residual, on=GROUP)
    bad[["res_area", "res_flux"]] = bad[["res_area", "res_flux"]].fillna(0.0)

    # Split each group's residual across its contaminated parts by AOI land area.
    share = bad.groupby(GROUP)["land_ha"].transform("sum")
    bad["w"] = (bad["land_ha"] / share).where(share > 0, 1.0 / bad.groupby(GROUP)["land_ha"].transform("size"))

    clamped = int((bad["res_area"] < 0).sum())
    bad["res_area"] = bad["res_area"].clip(lower=0.0)

    bad[AREA] = bad["res_area"] * bad["w"]
    bad[FLUX] = bad["res_flux"] * bad["w"]

    n_multi = int((bad.groupby(GROUP)["Geography_ID"].transform("nunique") > 1).sum())
    print(f"rows repaired by exact subtraction:     {len(bad) - n_multi:,}")
    print(f"rows repaired by area-share allocation: {n_multi:,}")
    print(f"negative residuals clamped to zero:     {clamped:,}")

    repaired = pd.concat([gap[~gap["bad"]], bad[gap.columns]], ignore_index=True)
    repaired = repaired.drop(columns=["bad"])
    repaired.to_csv(OUT, index=False)

    a0, a1 = gap[AREA].sum(), repaired[AREA].sum()
    f0, f1 = gap[FLUX].sum(), repaired[FLUX].sum()
    print(f"\nFRF area: {a0/1e6:,.2f} -> {a1/1e6:,.2f} Mha  ({(a1-a0)/1e6:+.2f})")
    print(f"FRF flux: {f0/1e6:,.1f} -> {f1/1e6:,.1f} Mt CO2e/yr  ({(f1-f0)/1e6:+.1f})")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
