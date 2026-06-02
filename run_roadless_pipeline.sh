#!/usr/bin/env bash
# Self-completing driver for the roadless-overlay FRF deliverable.
#
# Order is deliberate: the recommended recat_false (normal) mode runs first and
# gets compiled + summarized as soon as it finishes, so a usable deliverable
# exists partway through; recat_true (recategorize) follows, then a final
# compile/summarize/workbook over both modes.
#
# Batch steps use the ArcGIS Pro python (arcpy); compile/summarize/workbook use
# the geopandas/openpyxl python. Steps are NOT aborted on a single failure so a
# partial batch still gets summarized.

REPO="C:/GIS/git/learn_analysis"
ARCPY="/c/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe"
cd "$REPO" || exit 1

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

echo "[$(stamp)] === ROADLESS PIPELINE START ==="

echo "[$(stamp)] >>> normal (recat_false) batch — 4 periods"
"$ARCPY" run_padus_forests.py --mode normal

echo "[$(stamp)] >>> intermediate compile + summarize (recat_false only)"
python compile_padus_results.py
python summarize_padus_forests.py --rebuild

echo "[$(stamp)] >>> recategorize (recat_true) batch — 4 periods"
"$ARCPY" run_padus_forests.py --mode recategorize

echo "[$(stamp)] >>> final compile + summarize + workbook (both modes)"
python compile_padus_results.py
python summarize_padus_forests.py --rebuild
python build_frf_workbook.py

echo "[$(stamp)] === ROADLESS PIPELINE DONE ==="
