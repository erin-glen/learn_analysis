#!/usr/bin/env bash
# Resume the roadless FRF pipeline after an interruption (e.g. a reboot).
#
# Idempotent: for each (mode, period) it checks whether a COMPLETE
# combined_results.csv already exists (>1000 rows; a partial/in-progress period
# writes its csv only at the very end, and the old 10-feature test folder has
# ~130 rows) and runs only the periods that are still missing. Safe to re-run.

REPO="C:/GIS/git/learn_analysis"
ARCPY="/c/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe"
ROOT="C:/GIS/Data/LEARN/Disturbances/NLCD_harvest_severity/national_runs"
PERIODS="2013_2016 2016_2019 2019_2021 2021_2023"
cd "$REPO" || exit 1

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

# period_done <recat_dir> <period> -> exit 0 if a complete csv exists
period_done() {
  local dir="$1" period="$2" best=0 csv rows
  for d in "$ROOT/$dir"/*_${period}_PADUS_FS_BLM_ROADLESS_BatchProcessing; do
    [ -d "$d" ] || continue
    csv="$d/combined_results.csv"
    [ -f "$csv" ] || continue
    rows=$(($(wc -l < "$csv") - 1))
    [ "$rows" -gt "$best" ] && best=$rows
  done
  [ "$best" -gt 1000 ]
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
  if [ -n "$missing" ]; then
    echo "[$(stamp)] >>> $mode batch — periods: $missing"
    "$ARCPY" run_padus_forests.py --mode "$mode" --periods "$missing"
  else
    echo "[$(stamp)] $mode: all periods complete, nothing to run"
  fi
}

echo "[$(stamp)] === ROADLESS RESUME START ==="
run_mode normal recat_false
echo "[$(stamp)] >>> intermediate compile + summarize (recat_false)"
python compile_padus_results.py
python summarize_padus_forests.py --rebuild
run_mode recategorize recat_true
echo "[$(stamp)] >>> final compile + summarize + workbook (both modes)"
python compile_padus_results.py
python summarize_padus_forests.py --rebuild
python build_frf_workbook.py
echo "[$(stamp)] === ROADLESS RESUME DONE ==="
