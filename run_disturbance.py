# run_disturbances.py
"""
Orchestrates the ArcPy-based disturbance-processing workflow, in order:

1) insect_disease_merge.py
2) harvest_other.py
3) fire.py
4) final_disturbance.py

IMPORTANT: You must run 'insect_disease_process.py' in a separate
GDAL environment prior to this script.
"""

import logging

# Import each script
import insect_disease_merge
import harvest_other
import fire
import final_disturbance

def main():
    """
    Runs the ArcPy-based scripts in sequence.
    Make sure 'insect_disease_process.py' is already done.
    """
    logging.info("========== Disturbance Workflow Started ==========")
    logging.warning("Ensure 'insect_disease_process.py' has been run in the GDAL environment first.")

    # 1) Merge insect/disease
    insect_disease_merge.main()

    # 2) Harvest/other
    harvest_other.main()

    # 3) Fire
    fire.main()

    # 4) Final combination
    final_disturbance.main()

    logging.info("========== Disturbance Workflow Complete ==========")

if __name__ == "__main__":
    main()
