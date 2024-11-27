# batch_forest_analysis.py

import forests_analysis
from unittest.mock import patch

def run_analysis_for_period(year1, year2, mode=None):
    # Mock the input() calls in forests_analysis.py to return the desired years
    with patch('builtins.input', side_effect=[str(year1), str(year2)]):
        forests_analysis.main(mode)

if __name__ == "__main__":
    # Define the inventory periods
    inventory_periods = [
        (2013, 2016),
        (2016, 2019),
        (2019, 2021),
    ]

    for year1, year2 in inventory_periods:
        print(f"\nRunning analysis for inventory period {year1}-{year2}")
        run_analysis_for_period(year1, year2)
