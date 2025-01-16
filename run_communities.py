# batch_communities_analysis.py

import communities_analysis
from unittest.mock import patch

def run_analysis_for_aoi_and_period(year1, year2, aoi_name, tree_canopy_source):
    # Mock the input() calls in communities_analysis.py to return the desired inputs
    inputs = [
        str(year1),           # Input for 'Enter Year 1: '
        str(year2),           # Input for 'Enter Year 2: '
        aoi_name,             # Input for 'Enter the AOI name: '
        tree_canopy_source,   # Input for 'Select Tree Canopy source (NLCD, CBW, Local): '
    ]
    with patch('builtins.input', side_effect=inputs):
        communities_analysis.main()

if __name__ == "__main__":
    # Define the AOIs and inventory periods
    aois = ['Jefferson', 'Montgomery']
    # aois = ['Montgomery']
    inventory_periods = [
        (2013, 2016),
        (2016, 2019),
        (2019, 2021),
    ]
    tree_canopy_source = 'NLCD'  # Set tree canopy source to 'NLCD' for all runs

    for aoi_name in aois:
        for year1, year2 in inventory_periods:
            print(f"\nRunning analysis for AOI '{aoi_name}' and inventory period {year1}-{year2}")
            run_analysis_for_aoi_and_period(year1, year2, aoi_name, tree_canopy_source)
