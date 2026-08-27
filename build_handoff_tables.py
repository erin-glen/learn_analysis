"""
Generate the corrected manuscript tables for the logging-emissions paper handoff.

Writes ready-to-use CSVs into the handoff directory so the next person does not have
to re-derive anything. Every table here is recomputed from the authoritative runs:

  national protection  -> combined_results_PADUS_GAP_REPAIRED_ALL.csv
  NWFP protection      -> combined_results_PADUS_NWFP_GAP_ALL.csv
  national by region   -> combined_results_PADUS_GAP_REPAIRED_ALL.csv

Conventions applied throughout, and the reason each was needed:

  * recat_true only. Manuscript paragraph 63 fixes land cover as forest through
    temporary disturbance, which is what recategorize implements. recat_false would
    drop 13.68 TgC/yr of fire emissions on burned forest into a Forest Change bucket
    the manuscript never reports.
  * Duration weights 3/3/2/2 over a denominator of 10. The published tables used a
    simple 4-period mean (Table S3) or weights of 4/4/3/3 and 3/3/3/3 over 10
    (Table 5), and Table 2 summed the four annual rates instead of averaging at all.
  * TgC/yr, converted from t CO2e/yr at 12/44.
  * Forest Remaining Forest only, matching the manuscript's stated scope.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyogrio

NR = Path(r"C:\GIS\Data\LEARN\Disturbances\NLCD_harvest_severity\national_runs")
OUT = Path(r"C:\GIS\Data\LEARN\LOGGING_PAPER_HANDOFF\corrected_tables")
AOI_NAT = Path(r"C:\Users\Erin.Glen\GIS_Data\Federal_Forests_AOI\padus_fs_blm_conus_gap_roadless.shp")
AOI_NWFP = Path(r"C:\Users\Erin.Glen\GIS_Data\NWFP_AOI\padus_fs_blm_nwfp_gap.shp")

AREA = "Area (ha, total)"
FLUX = "GHG Flux (t CO2e/year)"
C_FACTOR = 12 / 44 / 1e6          # t CO2e/yr -> TgC/yr
PERIODS = ["2013_2016", "2016_2019", "2019_2021", "2021_2023"]
WEIGHTS = [3, 3, 2, 2]            # period lengths in years; denominator 10

TYPE_ORDER = ["Fire", "Harvest 0-25%", "Harvest 25-50%", "Harvest 50-75%",
              "Harvest 75-100%", "Insect/Disease", "Undisturbed"]
TYPE_LABEL = {"Fire": "Wildfire", "Insect/Disease": "Insect/disease"}

STATE_TO_FS_REGION = {
    "MT": "R1", "ND": "R1", "ID": "R1", "CO": "R2", "KS": "R2", "NE": "R2",
    "SD": "R2", "WY": "R2", "AZ": "R3", "NM": "R3", "NV": "R4", "UT": "R4",
    "CA": "R5", "OR": "R6", "WA": "R6",
    **{s: "R8" for s in "AL AR FL GA KY LA MS NC OK SC TN TX VA".split()},
    **{s: "R9" for s in ("CT DE IL IN IA ME MD MA MI MN MO NH NJ NY OH PA RI "
                         "VT WV WI").split()},
}


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df = df[(df["recat_mode"] == "recat_true") &
            (df["Category"] == "Forest Remaining Forest")].copy()
    for c in (AREA, FLUX):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def weighted(df: pd.DataFrame, col: str, scale: float) -> float:
    """Duration-weighted annual mean over 2013-2023 (weights 3/3/2/2, denominator 10)."""
    return sum(df.loc[df["inventory_period"] == p, col].sum() * w
               for p, w in zip(PERIODS, WEIGHTS)) / 10 * scale


def add_classes(df: pd.DataFrame, aoi_path: Path) -> pd.DataFrame:
    aoi = pyogrio.read_dataframe(aoi_path, read_geometry=False).set_index("unit_id")
    df["gap_sts"] = df["Geography_ID"].map(aoi["gap_sts"])
    df["roadless"] = df["Geography_ID"].map(aoi["roadless"])
    # GAP 2.5 = roadless over GAP 3/4. Roadless never demotes GAP 1/2.
    df["protection"] = [
        "2.5" if r == "Y" and g in ("3", "4") else g
        for g, r in zip(df["gap_sts"], df["roadless"])
    ]
    return df


def protection_table(df: pd.DataFrame, label: str) -> pd.DataFrame:
    names = {"1": "GAP 1 - permanent protection",
             "2": "GAP 2 - permanent, managed",
             "2.5": "GAP 2.5 - roadless (GAP 3 + roadless)",
             "3": "GAP 3 - multiple use",
             "4": "GAP 4 - no known mandate"}
    rows = []
    for cls in ["1", "2", "2.5", "3", "4"]:
        sub = df[df["protection"] == cls]
        if sub.empty:
            continue
        row = {"geography": label, "protection_class": names[cls],
               "forest_Mha": round(weighted(sub, AREA, 1) / 1e6, 3)}
        for t in TYPE_ORDER:
            row[TYPE_LABEL.get(t, t) + " (TgC/yr)"] = round(
                weighted(sub[sub["Type"] == t], FLUX, C_FACTOR), 3)
        row["NET (TgC/yr)"] = round(weighted(sub, FLUX, C_FACTOR), 3)
        rows.append(row)
    tot = {"geography": label, "protection_class": "TOTAL",
           "forest_Mha": round(weighted(df, AREA, 1) / 1e6, 3)}
    for t in TYPE_ORDER:
        tot[TYPE_LABEL.get(t, t) + " (TgC/yr)"] = round(
            weighted(df[df["Type"] == t], FLUX, C_FACTOR), 3)
    tot["NET (TgC/yr)"] = round(weighted(df, FLUX, C_FACTOR), 3)
    rows.append(tot)
    return pd.DataFrame(rows)


def by_period_table(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Replaces Table S3: disturbance class x period, with a duration-weighted average."""
    rows = []
    for t in TYPE_ORDER:
        sub = df[df["Type"] == t]
        row = {"geography": label, "disturbance_class": TYPE_LABEL.get(t, t)}
        for p in PERIODS:
            row[p] = round(sub.loc[sub["inventory_period"] == p, FLUX].sum() * C_FACTOR, 3)
        row["weighted_avg_2013_2023"] = round(weighted(sub, FLUX, C_FACTOR), 3)
        row["simple_mean_AS_PUBLISHED"] = round(
            sum(row[p] for p in PERIODS) / 4, 3)
        rows.append(row)
    tr = {"geography": label, "disturbance_class": "TOTAL"}
    for p in PERIODS:
        tr[p] = round(df.loc[df["inventory_period"] == p, FLUX].sum() * C_FACTOR, 3)
    tr["weighted_avg_2013_2023"] = round(weighted(df, FLUX, C_FACTOR), 3)
    tr["simple_mean_AS_PUBLISHED"] = round(sum(tr[p] for p in PERIODS) / 4, 3)
    rows.append(tr)
    return pd.DataFrame(rows)


def by_region_table(df: pd.DataFrame) -> pd.DataFrame:
    """Replaces Table 2's flux column: per-region annual mean, NOT a sum of period rates."""
    df = df.copy()
    df["region"] = [
        "BLM" if m == "BLM" else STATE_TO_FS_REGION.get(s, "UNK")
        for m, s in zip(df["mang_name"], df["state_nm"])
    ]
    rows = []
    for reg in sorted(df["region"].unique()):
        sub = df[df["region"] == reg]
        per = [round(sub.loc[sub["inventory_period"] == p, FLUX].sum() * C_FACTOR, 2)
               for p in PERIODS]
        rows.append({
            "region": reg,
            "forest_Mha": round(weighted(sub, AREA, 1) / 1e6, 3),
            **{p: v for p, v in zip(PERIODS, per)},
            "CORRECT_weighted_mean_TgC_yr": round(weighted(sub, FLUX, C_FACTOR), 2),
            "AS_PUBLISHED_sum_of_rates": round(sum(per), 2),
        })
    tot = df
    per = [round(tot.loc[tot["inventory_period"] == p, FLUX].sum() * C_FACTOR, 2)
           for p in PERIODS]
    rows.append({"region": "ALL", "forest_Mha": round(weighted(tot, AREA, 1) / 1e6, 3),
                 **{p: v for p, v in zip(PERIODS, per)},
                 "CORRECT_weighted_mean_TgC_yr": round(weighted(tot, FLUX, C_FACTOR), 2),
                 "AS_PUBLISHED_sum_of_rates": round(sum(per), 2)})
    return pd.DataFrame(rows)


def harvest_on_protected(nat: pd.DataFrame, nwfp: pd.DataFrame) -> pd.DataFrame:
    harv = [t for t in TYPE_ORDER if t.startswith("Harvest")]
    rows = []
    for label, df in (("National FS+BLM", nat), ("NWFP FS+BLM", nwfp)):
        prot = df[df["gap_sts"].isin(["1", "2"])]
        rows.append({
            "geography": label,
            "protected_forest_Mha": round(weighted(prot, AREA, 1) / 1e6, 3),
            "protected_share_of_area_pct": round(
                weighted(prot, AREA, 1) / weighted(df, AREA, 1) * 100, 1),
            "harvest_emissions_on_protected_TgC_yr": round(
                weighted(prot[prot["Type"].isin(harv)], FLUX, C_FACTOR), 3),
            "total_harvest_emissions_TgC_yr": round(
                weighted(df[df["Type"].isin(harv)], FLUX, C_FACTOR), 3),
            "protected_share_of_harvest_emissions_pct": round(
                weighted(prot[prot["Type"].isin(harv)], FLUX, C_FACTOR) /
                weighted(df[df["Type"].isin(harv)], FLUX, C_FACTOR) * 100, 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nat = add_classes(load(NR / "combined_results_PADUS_GAP_REPAIRED_ALL.csv"), AOI_NAT)
    nwfp = add_classes(load(NR / "combined_results_PADUS_NWFP_GAP_ALL.csv"), AOI_NWFP)

    writes = [
        ("table05_protection_national.csv", protection_table(nat, "National FS+BLM")),
        ("table05_protection_nwfp.csv", protection_table(nwfp, "NWFP FS+BLM")),
        ("tableS3_by_period_national.csv", by_period_table(nat, "National FS+BLM")),
        ("tableS3_by_period_nwfp.csv", by_period_table(nwfp, "NWFP FS+BLM")),
        ("table02_flux_by_region.csv", by_region_table(nat)),
        ("finding_harvest_on_protected_land.csv", harvest_on_protected(nat, nwfp)),
    ]
    for name, df in writes:
        df.to_csv(OUT / name, index=False)
        print(f"wrote {name:<44} {len(df):>3} rows")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
