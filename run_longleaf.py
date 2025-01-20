# run_longleaf.py

import longleaf_analysis
from unittest.mock import patch

def run_analysis_for_period(year1, year2, mode=None):
    """
    Helper function to run the longleaf analysis for a specific pair of years.
    Uses unittest.mock.patch to simulate interactive input for year1 and year2.
    """
    with patch('builtins.input', side_effect=[str(year1), str(year2)]):
        longleaf_analysis.main(mode)

if __name__ == "__main__":
    # Define the inventory periods you want to run
    inventory_periods = [
        (2001, 2004),
        (2004, 2006),
        (2008, 2011),
        (2011, 2013),
        (2013, 2016),
        (2016, 2019),
        (2019, 2021),
    ]

    for (year1, year2) in inventory_periods:
        print(f"\nRunning Longleaf analysis for {year1}-{year2}...")
        run_analysis_for_period(year1, year2)
