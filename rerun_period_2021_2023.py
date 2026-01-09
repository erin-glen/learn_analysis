#!/usr/bin/env python3
"""
Rerun the disturbance pipeline for the 2021_2023 period only.

This script:
  1) Limits processing to the 2021_2023 period.
  2) Optionally deletes existing outputs for that period (including TCC-derived
     change/severity rasters so they are recomputed).
  3) Executes insect merge, harvest, fire, and final combine steps.
"""

import argparse
import importlib
import logging
import os

import arcpy

import disturbance_config as cfg
import insect_disease_merge
import fire
import final_disturbance

PERIOD = "2021_2023"
PERIOD_ENDPOINTS = [2021, 2023]
PERIOD_YEARS = [2021, 2022, 2023]


def _apply_period_overrides() -> None:
    cfg.TIME_PERIODS_ALL = {PERIOD: PERIOD_ENDPOINTS}
    cfg.TIME_PERIODS_TCC = {PERIOD: PERIOD_ENDPOINTS}
    cfg.TIME_PERIODS = cfg.TIME_PERIODS_TCC
    cfg.INSECT_TIME_PERIODS = cfg.TIME_PERIODS_ALL
    cfg.FIRE_TIME_PERIODS = {PERIOD: PERIOD_YEARS}


def _delete_if_exists(path: str) -> None:
    if not path:
        return
    try:
        if arcpy.Exists(path):
            arcpy.management.Delete(path)
            logging.info("Deleted existing dataset: %s", path)
            return
    except Exception:
        pass
    if os.path.exists(path):
        os.remove(path)
        logging.info("Deleted existing file: %s", path)


def _harvest_workflows() -> list[str]:
    return getattr(
        cfg,
        "FINAL_HARVEST_WORKFLOWS",
        ["nlcd_tcc_severity", "nlcd_tcc_percent_severity"],
    )


def _clean_outputs() -> None:
    logging.info("Cleaning outputs for %s", PERIOD)

    _delete_if_exists(os.path.join(cfg.INSECT_FINAL_DIR, f"insect_damage_{PERIOD}.tif"))
    _delete_if_exists(os.path.join(cfg.NLCD_FINAL_INSECT_DIR, f"insect_{PERIOD}.tif"))
    _delete_if_exists(os.path.join(cfg.NLCD_FINAL_FIRE_DIR, f"fire_{PERIOD}.tif"))

    _delete_if_exists(os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{PERIOD}.tif"))
    for year in PERIOD_YEARS:
        _delete_if_exists(os.path.join(cfg.FIRE_OUTPUT_DIR, f"fire_{year}_reclass.tif"))

    for workflow in _harvest_workflows():
        hcfg = cfg.harvest_product_config(workflow)
        method_tag = hcfg.get("method_tag", "abs")
        harvest_path = cfg.harvest_raster_path(PERIOD, workflow=workflow)
        _delete_if_exists(harvest_path)

        out_combined = os.path.join(cfg.NLCD_FINAL_DIR, f"disturb_{method_tag}_{PERIOD}.tif")
        out_harvest_only = os.path.join(
            cfg.NLCD_FINAL_HARVEST_ONLY_DIR,
            f"harvest_counted_{method_tag}_{PERIOD}.tif",
        )
        _delete_if_exists(out_combined)
        _delete_if_exists(out_harvest_only)

        if "nlcd_tcc" in workflow:
            _delete_if_exists(
                os.path.join(cfg.NLCD_TCC_CHANGE_DIR, f"nlcd_tcc_change_{PERIOD}.tif")
            )
            _delete_if_exists(
                os.path.join(cfg.NLCD_HARVEST_SEVERITY_DIR, f"nlcd_tcc_severity_{PERIOD}.tif")
            )


def _load_harvest_module():
    harvest_cfg = cfg.harvest_product_config()
    module_name = harvest_cfg["module"]
    logging.info(
        "Selected harvest workflow '%s' => %s (module: %s)",
        cfg.HARVEST_WORKFLOW,
        harvest_cfg.get("description", ""),
        module_name,
    )
    return importlib.import_module(module_name)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-run disturbance processing for the 2021_2023 period.",
    )
    parser.add_argument(
        "--skip-clean",
        action="store_true",
        help="Do not delete existing outputs before processing.",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=["insect_merge", "harvest", "fire", "final"],
        help="Subset of steps to run (default: all).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.info("========== Rerun 2021_2023 Workflow Started ==========")

    _apply_period_overrides()

    if not args.skip_clean:
        _clean_outputs()

    steps_to_run = args.steps or ["insect_merge", "harvest", "fire", "final"]
    harvest_module = None

    for step in steps_to_run:
        logging.info("---- Running step: %s ----", step)
        if step == "insect_merge":
            insect_disease_merge.main()
        elif step == "harvest":
            if harvest_module is None:
                harvest_module = _load_harvest_module()
            try:
                harvest_module.main()
            except TypeError:
                harvest_module.main()
        elif step == "fire":
            fire.main()
        elif step == "final":
            final_disturbance.main()

    logging.info("========== Rerun 2021_2023 Workflow Complete ==========")


if __name__ == "__main__":
    main()