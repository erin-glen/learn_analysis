"""
Rebuild the PAD-US AOI with real GAP protection status, then the roadless overlay.

Why this exists
---------------
`prepare_padus_aoi.py` reads `PADUS4_1Fee` only. For USFS+BLM in CONUS that layer is
100.0% GAP 3 by acreage (GAP 1 = 81 ac, GAP 2 = 29,925 ac out of 343.9M ac), because
wilderness and the other congressionally reserved designations carry their GAP status
in the separate `PADUS4_1Designation` layer. The AOI therefore had no protection
signal at all: 75.75M acres of GAP 1/2 designations -- Wilderness (41.9M ac),
Wilderness Study Areas (13.3M ac), National Monuments (12.0M ac), National
Conservation Areas (5.1M ac) -- were invisible, and every acre of them was reported
as "GAP 3, not protected from commercial logging".

This script applies the strongest overlapping designation GAP to each fee polygon
BEFORE the roadless overlay, per the external reviewer's instruction.

Method
------
  1. Base geometry: reuse `padus_fs_blm_conus_cleaned.shp` (605 polygons,
     343,813,892 ac). Reusing it rather than rebuilding from the gdb keeps total
     area comparable with the previous deliverable, so old and new runs can be
     compared unit by unit.
  2. Designations: USFS+BLM, CONUS, GAP 1 and GAP 2 only. GAP 3 designations change
     nothing, and GAP 4 is weaker than the fee default so it must not demote.
     Non-FS/BLM GAP 1/2 designations (25.7M ac, almost all NPS and FWS) sit on their
     own land and are excluded.
  3. Carve each base polygon strongest-first: GAP 1 part, then GAP 2 from what
     remains, then the residual keeps its fee gap_sts. Parts sum back to the input.
  4. Roadless split via the existing `add_roadless_split`, which leaves gap_sts
     untouched -- so roadless stays a cross-cutting dimension and never demotes
     GAP 1/2, and the GAP-2.5 view remains derivable downstream.

Run with a python that has geopandas (the anaconda env, not arcgispro-py3).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely import make_valid
from shapely.ops import unary_union

from prepare_padus_aoi import (
    CONUS_STATES,
    GDB,
    MANAGERS,
    OUTPUT_DIR,
    RENAMED_KEY_COLS,
    SLIVER_ACRES_THRESHOLD,
    _polygons_only,
    add_roadless_split,
)

BASE_SHP = OUTPUT_DIR / "padus_fs_blm_conus_cleaned.shp"
OUTPUT_SHP = OUTPUT_DIR / "padus_fs_blm_conus_gap_roadless.shp"
QA_CSV = OUTPUT_DIR / "padus_fs_blm_conus_gap_roadless_qa.csv"

DESIG_LAYER = "PADUS4_1Designation"
ACRES_PER_M2 = 0.000247105
OVERLAY_GAPS = ("1", "2")  # strongest first


def load_designations(target_crs) -> dict:
    """USFS+BLM CONUS designations, split by GAP status, reprojected and repaired."""
    print("[2/5] loading " + DESIG_LAYER + " (GAP 1/2, USFS+BLM, CONUS)...")
    d = pyogrio.read_dataframe(
        GDB, layer=DESIG_LAYER,
        columns=["Mang_Name", "GAP_Sts", "State_Nm", "Des_Tp", "GIS_Acres"],
    )
    d = d[d["Mang_Name"].isin(MANAGERS)]
    d = d[d["State_Nm"].fillna("").str.upper().isin(CONUS_STATES)]
    d = d[d["GAP_Sts"].isin(OVERLAY_GAPS)].copy()
    print(f"      {len(d):,} designation records, {d['GIS_Acres'].sum():,.0f} reported acres")
    d = d.to_crs(target_crs)
    d["geometry"] = d.geometry.apply(make_valid).apply(_polygons_only)
    d = d[~d.geometry.is_empty & d.geometry.notna()]

    out = {}
    for gap in OVERLAY_GAPS:
        sub = d[d["GAP_Sts"] == gap]
        gdf = gpd.GeoDataFrame(sub[["Des_Tp", "geometry"]].copy(), geometry="geometry", crs=d.crs)
        out[gap] = gdf
        print(f"      GAP {gap}: {len(gdf):,} polygons, "
              f"{gdf.geometry.area.sum() * ACRES_PER_M2:,.0f} ac (may self-overlap)")
    return out


def _local_union(layer, sindex, geom):
    """Union only the layer polygons that actually touch `geom`, or None."""
    hits = list(sindex.query(geom, predicate="intersects"))
    if not hits:
        return None
    return make_valid(unary_union(layer.geometry.iloc[hits].values))


def add_gap_overlay(base: gpd.GeoDataFrame, desig: dict) -> gpd.GeoDataFrame:
    """
    Carve each base polygon into GAP 1 / GAP 2 / residual parts, strongest first.
    The residual keeps the polygon's own fee gap_sts. Parts sum back to the input.
    """
    print("[3/5] applying strongest overlapping designation GAP to each fee polygon...")
    idx = {gap: desig[gap].sindex for gap in OVERLAY_GAPS}
    rows, n = [], len(base)

    for i, (_, r) in enumerate(base.iterrows()):
        remaining = make_valid(r.geometry)
        parts = []

        for gap in OVERLAY_GAPS:
            if remaining is None or remaining.is_empty:
                break
            local = _local_union(desig[gap], idx[gap], remaining)
            if local is None:
                continue
            carved = _polygons_only(make_valid(remaining.intersection(local)))
            if not carved.is_empty:
                parts.append((carved, gap))
                remaining = _polygons_only(make_valid(remaining.difference(local)))

        if remaining is not None and not remaining.is_empty:
            parts.append((remaining, r["gap_sts"]))  # undesignated: keep fee status

        for geom, gap in parts:
            acres = geom.area * ACRES_PER_M2
            if acres < SLIVER_ACRES_THRESHOLD:
                continue
            nr = r.copy()
            nr["geometry"] = geom
            nr["gap_sts"] = str(gap)
            nr["acres_nat"] = round(acres, 1)
            rows.append(nr)

        if (i + 1) % 50 == 0 or i == n - 1:
            print(f"      processed {i + 1:,}/{n:,} base polygons -> {len(rows):,} parts")

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=base.crs).reset_index(drop=True)
    print(f"      {len(out):,} polygons after GAP overlay")
    print("      acres by new gap_sts:")
    for gap, a in out.groupby("gap_sts")["acres_nat"].sum().items():
        print(f"        GAP {gap}: {a:,.0f} ac")
    return out


def export(gdf: gpd.GeoDataFrame, base_acres: float) -> None:
    print("[5/5] writing -> " + str(OUTPUT_SHP))
    cols = RENAMED_KEY_COLS + ["roadless", "unit_id", "acres_nat", "geometry"]
    out = gdf[cols].copy()
    out.to_file(OUTPUT_SHP, driver="ESRI Shapefile")

    dupes = int(out["unit_id"].duplicated().sum())
    print(f"      unit_id unique: {dupes == 0} ({len(out):,} rows, {out['unit_id'].nunique():,} ids)")

    qa = (
        out.drop(columns="geometry")
        .groupby(["mang_name", "gap_sts", "roadless"], as_index=False)
        .agg(n_units=("unit_id", "nunique"), acres=("acres_nat", "sum"))
        .assign(acres=lambda d: d["acres"].round(0).astype(int))
    )
    qa.to_csv(QA_CSV, index=False)
    print("\n" + qa.to_string(index=False))

    tot = out["acres_nat"].sum()
    print(f"\ntotal polygons: {len(out):,}   (previous AOI: 903)")
    print(f"total acres:    {tot:,.0f}")
    print(f"base acres:     {base_acres:,.0f}")
    print(f"area conserved: {abs(tot - base_acres):,.1f} ac difference "
          f"({abs(tot - base_acres) / base_acres * 100:.4f}%)")
    prot = out.loc[out["gap_sts"].isin(["1", "2"]), "acres_nat"].sum()
    print(f"\nGAP 1/2 (protected): {prot:,.0f} ac = {prot * 0.404686 / 1e6:.2f} M ha"
          f"   [was 10,006 ac in the old AOI]")


def main() -> None:
    print("[1/5] loading base geometry " + BASE_SHP.name + "...")
    base = gpd.read_file(BASE_SHP)
    base_acres = base["acres_nat"].sum()
    print(f"      {len(base):,} polygons, {base_acres:,.0f} ac, {base.crs.name}")

    desig = load_designations(base.crs)
    carved = add_gap_overlay(base, desig)
    print("[4/5] roadless split (gap_sts preserved; roadless never demotes GAP 1/2)")
    split = add_roadless_split(carved, RENAMED_KEY_COLS)
    export(split, base_acres)


if __name__ == "__main__":
    main()
