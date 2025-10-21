#!/usr/bin/env python
"""
run_insect_disease.py

This script calls insect_disease_process.py as a subprocess
for a hardcoded list of time periods.

Simply edit HARDCODED_PERIODS below to add/remove periods.

Example
-------
```
python run_insect_disease.py
```
"""

import logging
import subprocess
import sys

# Hardcoded time periods to process:
HARDCODED_PERIODS = [
    "2019_2021"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def main():
    logging.info("========== Starting Insect/Disease Processing ==========")
    logging.info(f"Hardcoded time periods: {HARDCODED_PERIODS}")

    for period in HARDCODED_PERIODS:
        logging.info(f"\n--- Processing time period: {period} ---")
        cmd = [
            # Adjust the Python path if needed (e.g., use a specific GDAL python.exe)
            "python",
            "insect_disease_process.py",
            "--period",
            period
        ]
        run_subprocess(cmd)

    logging.info("All hardcoded periods processed successfully.")


def run_subprocess(cmd_list):
    """
    Helper to run a subprocess command, capturing and logging its output.
    """
    logging.info(f"Running command: {' '.join(cmd_list)}")
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, check=True)
        # Print or log stdout/stderr as needed:
        logging.info(result.stdout.strip())
        if result.stderr:
            logging.warning(result.stderr.strip())
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with return code {e.returncode}")
        logging.error(f"Stdout:\n{e.stdout}")
        logging.error(f"Stderr:\n{e.stderr}")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()