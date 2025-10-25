#!/usr/bin/env python3
"""Retroactively compress disturbance outputs using GDAL."""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence, List, Optional

import disturbance_config as cfg


DEFAULT_CREATION_OPTS = ("COMPRESS=LZW", "TILED=YES")


def _existing_dirs(dirs: Iterable[str]) -> List[str]:
    out: List[str] = []
    for d in dirs:
        if os.path.isdir(d):
            out.append(d)
        else:
            logging.warning("Skipping missing directory: %s", d)
    return out


def _default_dirs() -> List[str]:
    return [
        cfg.NLCD_FINAL_DIR,
        cfg.NLCD_FINAL_HARVEST_ONLY_DIR,
        cfg.NLCD_FINAL_INSECT_DIR,
        cfg.NLCD_FINAL_FIRE_DIR,
        cfg.NLCD_TCC_CHANGE_DIR,
        cfg.NLCD_TCC_PCT_CHANGE_DIR,
        cfg.NLCD_HARVEST_SEVERITY_DIR,
        cfg.NLCD_HARVEST_PCT_SEV_DIR,
    ]


def _find_tifs(directories: Sequence[str]) -> List[str]:
    rasters: List[str] = []
    for d in directories:
        for root, _, files in os.walk(d):
            for name in files:
                if name.lower().endswith(".tif"):
                    rasters.append(os.path.join(root, name))
    return sorted(rasters)


def _compress_raster(path: str, *, overwrite: bool, dry_run: bool, creation_opts: Sequence[str]) -> bool:
    logging.info("Compressing: %s", path)
    if dry_run:
        return True

    if shutil.which("gdal_translate") is None:
        raise RuntimeError("gdal_translate not found on PATH; install GDAL or adjust PATH.")

    directory = os.path.dirname(path)
    base = os.path.basename(path)
    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{base}", suffix=".tmp.tif", delete=False) as tmp:
        tmp_path = tmp.name

    cmd = ["gdal_translate", path, tmp_path]
    for opt in creation_opts:
        cmd.extend(["-co", opt])

    logging.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        logging.error("gdal_translate failed for %s\nSTDOUT: %s\nSTDERR: %s", path, result.stdout, result.stderr)
        os.unlink(tmp_path)
        return False

    if overwrite:
        os.replace(tmp_path, path)
    else:
        compressed_path = f"{os.path.splitext(path)[0]}_lzw.tif"
        os.replace(tmp_path, compressed_path)
        logging.info("Compressed copy written to %s", compressed_path)
    return True


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compress disturbance rasters using GDAL.")
    parser.add_argument("dirs", nargs="*", help="Directories to search for GeoTIFF files. Defaults to standard output folders.")
    parser.add_argument("--overwrite", action="store_true", help="Replace originals in-place (default writes *_lzw.tif copies).")
    parser.add_argument("--dry-run", action="store_true", help="List rasters that would be compressed without running GDAL.")
    parser.add_argument(
        "--creation-option",
        "-co",
        action="append",
        dest="creation_options",
        help="Creation options to pass to gdal_translate (may be repeated). Default: COMPRESS=LZW, TILED=YES.",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)s] %(message)s")

    dirs = args.dirs or _default_dirs()
    dirs = _existing_dirs(dirs)
    if not dirs:
        logging.error("No valid directories to scan.")
        return 1

    rasters = _find_tifs(dirs)
    if not rasters:
        logging.warning("No GeoTIFF files found in: %s", ", ".join(dirs))
        return 0

    creation_opts = args.creation_options or list(DEFAULT_CREATION_OPTS)

    success = True
    for path in rasters:
        ok = _compress_raster(path, overwrite=args.overwrite, dry_run=args.dry_run, creation_opts=creation_opts)
        success &= ok

    if args.dry_run:
        logging.info("Dry run complete. %d rasters would be processed.", len(rasters))
    elif success:
        logging.info("Compression complete. Processed %d rasters.", len(rasters))
    else:
        logging.error("Compression completed with errors.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
