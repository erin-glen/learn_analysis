#!/usr/bin/env bash
# Driver for the GAP-corrected rebuild: national AOI then the NWFP clip, recat_true only.
#
# Why: the previous AOIs were built from PADUS4_1Fee, which is 100% GAP 3 by acreage
# for USFS+BLM CONUS, so they carried no protection signal and reported all
# designated wilderness as "not protected from commercial logging". The GAP AOIs
# rebuild that from PADUS4_1Designation (prepare_padus_gap_aoi.py).
#
# recat_true only: that is the mode the manuscript's own method calls for (its
# paragraph 63 fixes land cover as forest through temporary disturbance, which is
# what recategorize implements). Running one mode instead of two halves the compute.
#
# Idempotent: a period whose combined_results.csv already covers >=95% of the AOI's
# features is skipped, so this is safe to re-run after an interruption. The feature
# count is read from the AOI itself rather than hardcoded.
#
# Child failures are recorded and the script exits NON-ZERO, and coverage is verified
# per AOI at the end -- the 2026-06-04 wrapper exited 0 on a completely failed batch.

REPO="C:/GIS/git/learn_analysis"
ARCPY="/c/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe"
PY="/c/Users/Erin.Glen/AppData/Local/anaconda3/python.exe"
ROOT="C:/GIS/Data/LEARN/Disturbances/NLCD_harvest_severity/national_runs"
PERIODS="2013_2016 2016_2019 2019_2021 2021_2023"

cd "$REPO" || exit 1
FAILED=""

stamp() { date "+%Y-%m-%d %H:%M:%S"; }
note_fail() { FAILED="${FAILED:+$FAILED, }$1"; echo "[$(stamp)] !!! FAILED: $1"; }

# feature_count <preset> -> number of features in that preset's AOI
feature_count() {
  "$PY" -c "
import os,sys,pyogrio
os.environ['LEARN_AOI']=sys.argv[1]
import compile_padus_results as c
print(len(pyogrio.read_dataframe(c.AOI_PRESETS[sys.argv[1]]['aoi_shp'],read_geometry=False)))
" "$1" 2>/dev/null
}

# period_done <label> <period> <threshold> -> 0 if a complete combined_results.csv exists.
# Parsed with pandas, not awk: the header contains a quoted "Area (ha, total)" whose
# embedded comma shifts every naive field index.
period_done() {
  local label="$1" period="$2" thresh="$3" best=0 csv n
  for d in "$ROOT/recat_true"/*_${period}_${label}_BatchProcessing; do
    [ -d "$d" ] || continue
    csv="$d/combined_results.csv"
    [ -f "$csv" ] || continue
    n=$("$PY" -c "import sys,pandas as pd; print(pd.read_csv(sys.argv[1],low_memory=False)['Geography_ID'].nunique())" "$csv" 2>/dev/null)
    [ "${n:-0}" -gt "$best" ] && best=$n
  done
  [ "$best" -ge "$thresh" ]
}

run_aoi() {
  local preset="$1" label="$2"
  export LEARN_AOI="$preset"

  local nfeat thresh
  nfeat=$(feature_count "$preset")
  if [ -z "$nfeat" ] || [ "$nfeat" -lt 1 ]; then
    note_fail "$preset: could not read AOI feature count"
    return 1
  fi
  thresh=$(( nfeat * 95 / 100 ))
  echo ""
  echo "[$(stamp)] ================ AOI $preset ($label) ================"
  echo "[$(stamp)] $nfeat features; completion threshold $thresh"

  echo "[$(stamp)] >>> preflight"
  if ! "$ARCPY" preflight_aoi.py "$preset"; then
    note_fail "$preset preflight"
    return 1
  fi

  local missing=""
  for p in $PERIODS; do
    if period_done "$label" "$p" "$thresh"; then
      echo "[$(stamp)] skip $p (already complete)"
    else
      missing="${missing:+$missing,}$p"
    fi
  done

  if [ -n "$missing" ]; then
    # One arcpy process PER PERIOD, not one for all four. Measured on the first GAP
    # run: sharing a process across periods degraded throughput ~30% per period
    # (10.3 -> 13.1 -> 17.1 hr, and period 4 was tracking ~29 hr) as arcpy scratch
    # state accumulated over 50+ hours. A fresh process per period holds period-1
    # speed throughout, roughly a 40% saving on a 4-period batch.
    echo "[$(stamp)] >>> recategorize batch - periods: $missing (one process per period)"
    for per in $(echo "$missing" | tr ',' ' '); do
      echo "[$(stamp)] --- period $per ---"
      if ! "$ARCPY" run_padus_forests.py --aoi "$preset" --mode recategorize --periods "$per"; then
        note_fail "$preset recategorize $per"
      fi
    done
  else
    echo "[$(stamp)] all periods already complete"
  fi

  echo "[$(stamp)] >>> compile + summarize + workbook"
  "$PY" compile_padus_results.py    || note_fail "$preset compile"
  "$PY" summarize_padus_forests.py --rebuild || note_fail "$preset summarize"
  "$PY" build_frf_workbook.py       || note_fail "$preset workbook"

  echo "[$(stamp)] >>> verifying coverage"
  local done=0
  for p in $PERIODS; do
    if period_done "$label" "$p" "$thresh"; then
      echo "    OK      recat_true/$p"; done=$((done + 1))
    else
      echo "    MISSING recat_true/$p"
    fi
  done
  echo "[$(stamp)] $preset coverage: $done/4"
  [ "$done" -ne 4 ] && note_fail "$preset coverage $done/4"
  return 0
}

echo "[$(stamp)] ===== GAP PIPELINE START (recat_true only) ====="
run_aoi gap      PADUS_FS_BLM_GAP
run_aoi nwfp_gap PADUS_FS_BLM_NWFP_GAP

echo ""
if [ -n "$FAILED" ]; then
  echo "[$(stamp)] ===== GAP PIPELINE FINISHED WITH FAILURES: $FAILED ====="
  exit 1
fi
echo "[$(stamp)] ===== GAP PIPELINE DONE ====="
