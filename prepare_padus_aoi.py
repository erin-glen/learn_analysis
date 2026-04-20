"""
Prepare a PAD-US-based AOI shapefile for LEARN `run_forests` / `offline_analysis.py`.

Output is one row per (Mang_Name x Unit_Nm x GAP_Sts x State_Nm) so downstream
LEARN results can be rolled up by manager, unit, GAP status, or state.

Pipeline:
  1. Scope: PADUS4_1Fee, Mang_Name in {USFS, BLM}, State_Nm in CONUS.
  2. Geometry repair: make_valid, drop empty, drop slivers below threshold.
  3. Group key: Mang_Name + Unit_Nm + GAP_Sts + State_Nm, with stable unit_id hash.
  4. Within-group union: collapse same-key fragments to one MultiPolygon.
  5. Between-group overlap resolution: priority-stacked difference.
     Priority = (GAP_Sts asc, acres asc) -> more-protected / more-specific wins,
     each acre is attributed to exactly one row.
  6. Export shapefile in native PAD-US Albers (matches NLCD), plus QA CSV.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon

# ---------- configuration ----------
GDB = Path(r"C:\Users\Erin.Glen\GIS_Data\PADUS4_1\PADUS4_1Geodatabase.gdb")
LAYER = "PADUS4_1Fee"
OUTPUT_DIR = Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI")
OUTPUT_SHP = OUTPUT_DIR / "padus_fs_blm_conus_cleaned.shp"
QA_CSV = OUTPUT_DIR / "padus_fs_blm_conus_cleaned_qa.csv"

MANAGERS = ("USFS", "BLM")

CONUS_STATES = {
    "AL","AR","AZ","CA","CO","CT","DC","DE","FL","GA","IA","ID","IL","IN",
    "KS","KY","LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE",
    "NH","NJ","NM","NV","NY","OH","OK","OR","PA","RI","SC","SD","TN","TX",
    "UT","VA","VT","WA","WI","WV","WY",
}

SLIVER_ACRES_THRESHOLD = 0.25  # ~0.1 ha; drop features below this

KEY_COLS = ["Mang_Name", "Unit_Nm", "GAP_Sts", "State_Nm"]
KEEP_COLS = KEY_COLS + ["Loc_Nm", "Des_Tp", "Own_Name", "GIS_Acres"]

# Shapefile DBF field names must be <=10 chars.
FIELD_MAP = {
    "Mang_Name": "mang_name",
    "Unit_Nm":   "unit_nm",
    "GAP_Sts":   "gap_sts",
    "State_Nm":  "state_nm",
}


def load_subset() -> gpd.GeoDataFrame:
    print(f"[1/6] loading {LAYER} filtered to managers={MANAGERS}...")
    where = "Mang_Name IN ('" + "','".join(MANAGERS) + "')"
    gdf = pyogrio.read_dataframe(GDB, layer=LAYER, where=where, columns=KEEP_COLS)
    print(f"      loaded {len(gdf):,} features (all states)")
    gdf = gdf[gdf["State_Nm"].isin(CONUS_STATES)].copy()
    print(f"      after CONUS filter: {len(gdf):,} features")
    return gdf


def repair_and_filter(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print("[2/6] repairing geometry and dropping slivers...")
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    # make_valid can yield GeometryCollections; keep only polygonal parts.
    gdf["geometry"] = gdf.geometry.apply(_polygons_only)
    gdf = gdf[~gdf.geometry.is_empty].copy()
    # Recompute area from geometry (native Albers is metric). 1 m^2 = 0.000247105 acres.
    gdf["acres_calc"] = gdf.geometry.area * 0.000247105
    before = len(gdf)
    gdf = gdf[gdf["acres_calc"] >= SLIVER_ACRES_THRESHOLD].copy()
    print(f"      dropped {before - len(gdf):,} sliver/empty features; "
          f"{len(gdf):,} remain")
    return gdf


def _polygons_only(geom):
    """Return a (Multi)Polygon keeping only polygonal parts of a geometry."""
    if geom is None or geom.is_empty:
        return geom
    gt = geom.geom_type
    if gt in ("Polygon", "MultiPolygon"):
        return geom
    if gt == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not parts:
            return Polygon()
        if len(parts) == 1:
            return parts[0]
        # Flatten nested multipolygons.
        flat = []
        for p in parts:
            if p.geom_type == "MultiPolygon":
                flat.extend(p.geoms)
            else:
                flat.append(p)
        return MultiPolygon(flat)
    return Polygon()  # discard lines/points


def _stable_id(values: tuple[str, ...]) -> str:
    h = hashlib.sha1("|".join("" if v is None else str(v) for v in values).encode())
    return h.hexdigest()[:12]


def dissolve_by_key(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    print(f"[3/6] dissolving within groups on {KEY_COLS}...")
    # Normalize key fields for grouping (nulls -> sentinel so they don't vanish).
    for c in KEY_COLS:
        gdf[c] = gdf[c].fillna("UNK").astype(str).str.strip()
    dissolved = gdf.dissolve(by=KEY_COLS, as_index=False, aggfunc={"GIS_Acres": "sum"})
    dissolved["geometry"] = dissolved.geometry.apply(make_valid).apply(_polygons_only)
    dissolved["acres_nat"] = (dissolved.geometry.area * 0.000247105).round(1)
    dissolved["unit_id"] = dissolved.apply(
        lambda r: _stable_id(tuple(r[c] for c in KEY_COLS)), axis=1
    )
    print(f"      {len(dissolved):,} groups after within-group union")
    return dissolved


def resolve_overlaps(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Priority-stacked difference: sort by (GAP_Sts asc, acres asc) and subtract
    each higher-priority geometry from all lower-priority ones. Output is
    non-overlapping; every acre is attributed to exactly one row.
    """
    print("[4/6] resolving between-group overlaps (priority-stacked difference)...")
    work = gdf.copy()
    work["gap_int"] = pd.to_numeric(work["GAP_Sts"], errors="coerce").fillna(99).astype(int)
    work = work.sort_values(["gap_int", "acres_nat"], ascending=[True, True]).reset_index(drop=True)

    kept_geoms: list = []
    running_union = None
    from shapely.ops import unary_union

    for i, row in work.iterrows():
        g = row.geometry
        if running_union is not None:
            g = g.difference(running_union)
        g = make_valid(g)
        g = _polygons_only(g)
        kept_geoms.append(g)
        # Accumulate union for subsequent subtractions.
        running_union = g if running_union is None else unary_union([running_union, g])
        if (i + 1) % 50 == 0 or i == len(work) - 1:
            print(f"      processed {i + 1:,}/{len(work):,}")

    work["geometry"] = kept_geoms
    work = gpd.GeoDataFrame(work, geometry="geometry", crs=gdf.crs)
    # Refresh area and drop any rows that were fully subtracted away.
    work["acres_nat"] = (work.geometry.area * 0.000247105).round(1)
    before = len(work)
    work = work[work["acres_nat"] >= SLIVER_ACRES_THRESHOLD].copy()
    print(f"      dropped {before - len(work):,} rows reduced to slivers; "
          f"{len(work):,} rows remain")
    return work.drop(columns=["gap_int"])


def export(gdf: gpd.GeoDataFrame) -> None:
    print(f"[5/6] writing shapefile -> {OUTPUT_SHP}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = gdf.rename(columns=FIELD_MAP)[
        list(FIELD_MAP.values()) + ["unit_id", "acres_nat", "geometry"]
    ]
    out.to_file(OUTPUT_SHP, driver="ESRI Shapefile")

    print(f"[6/6] writing QA summary -> {QA_CSV}")
    qa = (
        out.drop(columns="geometry")
        .groupby(["mang_name", "gap_sts"], as_index=False)
        .agg(n_units=("unit_id", "nunique"), acres=("acres_nat", "sum"))
        .assign(acres=lambda d: d["acres"].round(0).astype(int))
    )
    qa.to_csv(QA_CSV, index=False)
    print(qa.to_string(index=False))
    print(f"\ntotal rows: {len(out):,}")
    print(f"total acres: {out['acres_nat'].sum():,.0f}")


def main() -> None:
    gdf = load_subset()
    gdf = repair_and_filter(gdf)
    dissolved = dissolve_by_key(gdf)
    resolved = resolve_overlaps(dissolved)
    export(resolved)


if __name__ == "__main__":
    main()
