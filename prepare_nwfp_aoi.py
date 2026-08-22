"""
Prepare an NWFP-clipped AOI shapefile for `run_padus_forests.py`.

The Northwest Forest Plan boundary supplied by the team (`dissolved_nfwp.shp`) is a
single dissolved polygon carrying no attributes, so on its own it yields one total
per inventory period and no breakdowns. To reproduce the dimension set from the
roadless deliverable we clip the already-attributed PAD-US FS+BLM roadless AOI to
the NWFP boundary: each output polygon is the NWFP portion of one PAD-US unit and
keeps that unit's mang_name / unit_nm / gap_sts / state_nm / roadless / unit_id, so
every downstream rollup works unchanged.

`unit_id` is deliberately carried over from the national AOI (not regenerated) so a
unit's NWFP results can be joined straight back to its national-run results. The
intersection is 1:1 with the source polygons, so the ids stay unique.

Pipeline:
  1. Load the dissolved NWFP boundary, reproject to the PAD-US AOI CRS.
  2. Explode it into parts so a spatial index can narrow each AOI polygon to the
     few NWFP parts that actually touch it (same trick as the roadless split).
  3. Intersect each PAD-US polygon with the local NWFP union; drop empties/slivers.
  4. Recompute acres_nat to the clipped area, keep the national acreage as
     acres_full and the retained share as nwfp_frac.
  5. Export shapefile in the PAD-US Albers (matches NLCD), plus a QA CSV.

Run with a python that has geopandas (the anaconda env, not arcgispro-py3):
    "%LOCALAPPDATA%\anaconda3\python.exe" prepare_nwfp_aoi.py
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.ops import unary_union

from prepare_padus_aoi import _polygons_only

# ---------- configuration ----------
NWFP_SHP = Path(r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\dissolved_nfwp.shp")
PADUS_SHP = Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_roadless.shp")
OUTPUT_DIR = Path(r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI")
OUTPUT_SHP = OUTPUT_DIR / "padus_fs_blm_nwfp.shp"
QA_CSV = OUTPUT_DIR / "padus_fs_blm_nwfp_qa.csv"

ACRES_PER_M2 = 0.000247105
SLIVER_ACRES_THRESHOLD = 0.25  # ~0.1 ha; matches prepare_padus_aoi

ATTR_COLS = ["mang_name", "unit_nm", "gap_sts", "state_nm", "roadless", "unit_id"]


def load_nwfp(target_crs) -> gpd.GeoDataFrame:
    """Load the dissolved NWFP boundary, reproject, and explode into indexable parts."""
    print(f"[2/4] loading {NWFP_SHP.name}...")
    nw = gpd.read_file(NWFP_SHP)
    print(f"      {len(nw)} feature(s) in {nw.crs.name}")
    nw = nw.to_crs(target_crs)
    total_ac = nw.geometry.area.sum() * ACRES_PER_M2
    parts = nw.explode(index_parts=False).reset_index(drop=True)
    parts["geometry"] = parts.geometry.apply(make_valid).apply(_polygons_only)
    parts = parts[~parts.geometry.is_empty & parts.geometry.notna()]
    parts = gpd.GeoDataFrame(parts[["geometry"]], geometry="geometry", crs=nw.crs)
    print(f"      {len(parts):,} NWFP parts after explode; {total_ac:,.0f} ac total")
    return parts, total_ac


def clip_to_nwfp(aoi: gpd.GeoDataFrame, nwfp: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Intersect each AOI polygon with the NWFP boundary, keeping its attributes."""
    print("[3/4] clipping AOI polygons to the NWFP boundary...")
    sindex = nwfp.sindex
    rows = []
    for n, (_, row) in enumerate(aoi.iterrows(), start=1):
        g = make_valid(row.geometry)
        hits = list(sindex.query(g, predicate="intersects"))
        if hits:
            local = make_valid(unary_union(nwfp.geometry.iloc[hits].values))
            clipped = _polygons_only(make_valid(g.intersection(local)))
            if not clipped.is_empty:
                acres = clipped.area * ACRES_PER_M2
                if acres >= SLIVER_ACRES_THRESHOLD:
                    rec = {c: row[c] for c in ATTR_COLS}
                    rec["acres_nat"] = round(acres, 1)
                    rec["acres_full"] = row["acres_nat"]
                    rec["geometry"] = clipped
                    rows.append(rec)
        if n % 100 == 0 or n == len(aoi):
            print(f"      screened {n:,}/{len(aoi):,} AOI polygons; {len(rows):,} kept")

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=aoi.crs)
    out["nwfp_frac"] = (out["acres_nat"] / out["acres_full"]).round(4)
    return out


def export(out: gpd.GeoDataFrame, nwfp_total_ac: float) -> None:
    """Write the clipped AOI + QA summary, and report NWFP coverage."""
    print(f"[4/4] writing shapefile -> {OUTPUT_SHP}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ATTR_COLS + ["acres_nat", "acres_full", "nwfp_frac", "geometry"]
    out = out[cols].copy()
    out.to_file(OUTPUT_SHP, driver="ESRI Shapefile")

    dupes = int(out["unit_id"].duplicated().sum())
    print(f"      unit_id unique: {dupes == 0} ({len(out):,} rows, "
          f"{out['unit_id'].nunique():,} distinct ids)")

    qa = (
        out.drop(columns="geometry")
        .groupby(["mang_name", "gap_sts", "roadless"], as_index=False)
        .agg(n_units=("unit_id", "nunique"), acres=("acres_nat", "sum"))
        .assign(acres=lambda d: d["acres"].round(0).astype(int))
    )
    qa.to_csv(QA_CSV, index=False)
    print("\n" + qa.to_string(index=False))

    by_state = (
        out.drop(columns="geometry")
        .groupby("state_nm", as_index=False)
        .agg(n_units=("unit_id", "nunique"), acres=("acres_nat", "sum"))
        .sort_values("acres", ascending=False)
    )
    print("\n" + by_state.to_string(index=False))

    kept = out["acres_nat"].sum()
    print(f"\ntotal rows:  {len(out):,}")
    print(f"NWFP total:  {nwfp_total_ac:,.0f} ac")
    print(f"FS+BLM kept: {kept:,.0f} ac ({kept / nwfp_total_ac * 100:.1f}% of NWFP)")
    print(f"not FS/BLM:  {nwfp_total_ac - kept:,.0f} ac "
          f"({(nwfp_total_ac - kept) / nwfp_total_ac * 100:.1f}%) "
          f"- mostly NPS/FWS, outside this AOI")
    print(f"  of which roadless: "
          f"{out.loc[out['roadless'] == 'Y', 'acres_nat'].sum():,.0f} ac")


def main() -> None:
    print(f"[1/4] loading {PADUS_SHP.name}...")
    aoi = gpd.read_file(PADUS_SHP)
    print(f"      {len(aoi):,} polygons in {aoi.crs.name}")
    missing = [c for c in ATTR_COLS if c not in aoi.columns]
    if missing:
        raise SystemExit(f"AOI is missing expected columns: {missing}")

    nwfp, nwfp_total_ac = load_nwfp(aoi.crs)
    out = clip_to_nwfp(aoi, nwfp)
    export(out, nwfp_total_ac)


if __name__ == "__main__":
    main()
