#!/usr/bin/env bash
# Self-completing driver for the NWFP (Northwest Forest Plan) FRF deliverable.
#
# Same shape as run_roadless_pipeline.sh: recat_false (normal) runs first and gets
# compiled + summarized immediately so a usable deliverable exists partway through,
# then recat_true (recategorize), then a final compile/summarize/workbook over both
# modes.
#
# Differences from the roadless wrapper, both lessons from the 2026-06-04 run:
#   1. The arcpy license is preflighted before anything long starts. That run died
#      instantly on "Product License has not been initialized" after the token
#      dropped during a previous multi-hour batch.
#   2. Child failures are recorded and the script exits NON-ZERO, and it verifies
#      8/8 (mode x period) coverage at the end. The roadless wrapper exited 0 even
#      with a completely failed batch, so a 4/8 run looked like a success.
# Individual steps still continue past a failure, so a partial batch is summarized.
#
# Idempotent: a (mode, period) whose combined_results.csv already covers >=90 of
# the 94 AOI features is skipped, so this is safe to re-run after an interruption.

REPO="C:/GIS/git/learn_analysis"
ARCPY="/c/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe"
ROOT="C:/GIS/Data/LEARN/Disturbances/NLCD_harvest_severity/national_runs"
PERIODS="2013_2016 2016_2019 2019_2021 2021_2023"
LABEL="PADUS_FS_BLM_NWFP"
MIN_FEATURES=90          # of 94; a complete period writes all of them at once

export LEARN_AOI=nwfp    # drives run_padus_forests --aoi default AND compile/summarize

cd "$REPO" || exit 1
FAILED=""

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

note_fail() {
  FAILED="${FAILED:+$FAILED, }$1"
  echo "[$(stamp)] !!! FAILED: $1"
}

# period_done <recat_dir> <period> -> 0 if a complete combined_results.csv exists.
# Counts distinct Geography_ID rather than rows, so a short run can't pass.
# Parsed with pandas, not awk: the header contains a quoted "Area (ha, total)",
# whose embedded comma shifts every naive field index and made this always
# return 1 -- which reported 0/8 coverage at the end of a fully successful run.
period_done() {
  local dir="$1" period="$2" best=0 csv n
  for d in "$ROOT/$dir"/*_${period}_${LABEL}_BatchProcessing; do
    [ -d "$d" ] || continue
    csv="$d/combined_results.csv"
    [ -f "$csv" ] || continue
    n=$(python -c "import sys,pandas as pd; print(pd.read_csv(sys.argv[1],low_memory=False)['Geography_ID'].nunique())" "$csv" 2>/dev/null)
    [ "${n:-0}" -gt "$best" ] && best=$n
  done
  [ "$best" -ge "$MIN_FEATURES" ]
}

run_mode() {
  local mode="$1" dir="$2" missing=""
  for p in $PERIODS; do
    if period_done "$dir" "$p"; then
      echo "[$(stamp)] skip $mode/$p (already complete)"
    else
      missing="${missing:+$missing,}$p"
    fi
  done
  if [ -z "$missing" ]; then
    echo "[$(stamp)] $mode: all periods complete, nothing to run"
    return 0
  fi
  echo "[$(stamp)] >>> $mode batch - periods: $missing"
  if ! "$ARCPY" run_padus_forests.py --aoi nwfp --mode "$mode" --periods "$missing"; then
    note_fail "$mode batch (periods: $missing)"
  fi
}

echo "[$(stamp)] === NWFP PIPELINE START (LEARN_AOI=$LEARN_AOI) ==="

# ---- preflight: arcpy license must be available before any long batch ----
echo "[$(stamp)] >>> preflight: arcpy license + AOI"
if ! "$ARCPY" preflight_aoi.py nwfp; then
  echo "[$(stamp)] === ABORTING: preflight failed (see above) ==="
  exit 1
fi

run_mode normal recat_false

echo "[$(stamp)] >>> intermediate compile + summarize (recat_false)"
python compile_padus_results.py || note_fail "intermediate compile"
python summarize_padus_forests.py --rebuild || note_fail "intermediate summarize"

run_mode recategorize recat_true

echo "[$(stamp)] >>> final compile + summarize + workbook (both modes)"
python compile_padus_results.py || note_fail "final compile"
python summarize_padus_forests.py --rebuild || note_fail "final summarize"
python build_frf_workbook.py || note_fail "workbook"

# ---- verify coverage: all 8 (mode x period) combinations must be complete ----
echo "[$(stamp)] >>> verifying coverage"
DONE=0
for pair in "normal recat_false" "recategorize recat_true"; do
  set -- $pair
  for p in $PERIODS; do
    if period_done "$2" "$p"; then
      echo "    OK      $2/$p"
      DONE=$((DONE + 1))
    else
      echo "    MISSING $2/$p"
    fi
  done
done
echo "[$(stamp)] coverage: $DONE/8"

if [ -n "$FAILED" ]; then
  echo "[$(stamp)] === NWFP PIPELINE FINISHED WITH FAILURES: $FAILED ==="
  exit 1
fi
if [ "$DONE" -ne 8 ]; then
  echo "[$(stamp)] === NWFP PIPELINE INCOMPLETE: coverage $DONE/8 ==="
  exit 1
fi
echo "[$(stamp)] === NWFP PIPELINE DONE (8/8) ==="
