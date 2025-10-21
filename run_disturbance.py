# run_disturbances.py
"""
Orchestrates the ArcPy-based disturbance-processing workflow, in order:

1) insect_disease_merge.py
2) harvest workflow (harvest_other.py or harvest_other_severity.py)
3) fire.py
4) final_disturbance.py

IMPORTANT: You must run 'insect_disease_process.py' in a separate
GDAL environment prior to this script.

Example
-------
Run only the harvest and final combination steps for two Hansen tiles:

```
python run_disturbance.py --steps harvest final --tile-id GFW2023_40N_090W --tile-id 40N_100W
```

Omit ``--steps`` to execute the full pipeline.
"""

import logging

# Import each script
import importlib
import argparse
from typing import Iterable, Sequence

import disturbance_config as cfg
import insect_disease_merge
import fire
import final_disturbance

STEP_SEQUENCE = [
    ("insect_merge", "Insect/disease merge", insect_disease_merge.main),
    ("harvest", "Harvest workflow", None),  # special handling below
    ("fire", "Fire processing", fire.main),
    ("final", "Final disturbance combination", final_disturbance.main),
]


def _load_harvest_module():
    harvest_cfg = cfg.harvest_product_config()
    module_name = harvest_cfg["module"]
    logging.info(
        "Selected harvest workflow '%s' => %s (module: %s)",
        cfg.HARVEST_WORKFLOW,
        harvest_cfg.get("description", ""),
        module_name,
    )
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"Unable to import harvest workflow module '{module_name}'"
        ) from exc


def _parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the disturbance-processing workflow end-to-end or for specific steps.",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=[name for name, _, _ in STEP_SEQUENCE],
        help=(
            "Names of the steps to run. Choices: "
            + ", ".join(name for name, _, _ in STEP_SEQUENCE)
        ),
    )
    parser.add_argument(
        "--tile-id",
        dest="tile_ids",
        action="append",
        default=[],
        metavar="TILE_ID",
        help="Optional Hansen tile id filter passed through to the harvest step.",
    )
    parser.add_argument(
        "--tiles",
        dest="tile_csv",
        metavar="ID1,ID2",
        help="Comma-separated list of tile ids to include (forwarded to harvest).",
    )
    return parser.parse_args(argv)


def _combine_tile_args(tile_ids: Iterable[str], csv: str | None) -> list[str]:
    combined = list(tile_ids) if tile_ids else []
    if csv:
        combined.extend(part.strip() for part in csv.split(",") if part.strip())
    return combined


def main(selected_steps: Sequence[str] | None = None, tile_ids: Iterable[str] | None = None):
    """
    Runs the ArcPy-based scripts in sequence.
    Make sure 'insect_disease_process.py' is already done.
    """
    logging.info("========== Disturbance Workflow Started ==========")
    logging.warning("Ensure 'insect_disease_process.py' has been run in the GDAL environment first.")

    steps_to_run = list(selected_steps) if selected_steps else [name for name, _, _ in STEP_SEQUENCE]
    valid_steps = {name for name, _, _ in STEP_SEQUENCE}
    invalid = [step for step in steps_to_run if step not in valid_steps]
    if invalid:
        raise ValueError(f"Unknown step name(s): {invalid}")

    harvest_module = None
    for step_name, step_label, step_func in STEP_SEQUENCE:
        if step_name not in steps_to_run:
            logging.info("Skipping %s step (not requested).", step_label)
            continue

        logging.info("---- Running step: %s ----", step_label)
        if step_name == "harvest":
            if harvest_module is None:
                harvest_module = _load_harvest_module()
            try:
                harvest_module.main(tile_ids=tile_ids)
            except TypeError:
                # Backwards compatibility if workflow has not been updated to accept tile_ids
                harvest_module.main()
        else:
            step_func()

    logging.info("========== Disturbance Workflow Complete ==========")

if __name__ == "__main__":
    args = _parse_cli_args()
    combined_tiles = _combine_tile_args(args.tile_ids, args.tile_csv)
    main(selected_steps=args.steps, tile_ids=combined_tiles or None)