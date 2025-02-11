# run_communities.py

import communities_analysis
from unittest.mock import patch

def run_analysis_for_aoi_and_period(year1, year2, aoi_name, tree_canopy_source, recategorize=False):
    """
    Mocks user inputs for communities_analysis.main().
    1) year1                => "Enter Year 1:"
    2) year2                => "Enter Year 2:"
    3) aoi_name             => "Enter the AOI name:"
    4) tree_canopy_source   => "Select Tree Canopy source (NLCD, CBW, Local):"
    5) recategorize prompt  => "Enable recategorization mode? (y/n):"
    """
    inputs = [
        str(year1),
        str(year2),
        aoi_name,
        tree_canopy_source,
        'y' if recategorize else 'n'
    ]
    with patch('builtins.input', side_effect=inputs):
        communities_analysis.main()

if __name__ == "__main__":
    # Define the AOIs and inventory periods
    aois = ['Jefferson']
    inventory_periods = [(2013, 2016)]
    tree_canopy_source = 'NLCD'  # Set tree canopy source to 'NLCD' for all runs

    # Choose whether to recategorize "Forest to Grassland" with disturbances => "Forest Remaining Forest"
    recategorize_mode = True

    for aoi_name in aois:
        for (year1, year2) in inventory_periods:
            print(f"\nRunning analysis for AOI '{aoi_name}' and inventory period {year1}-{year2}, "
                  f"recategorize={recategorize_mode}")
            run_analysis_for_aoi_and_period(
                year1=year1,
                year2=year2,
                aoi_name=aoi_name,
                tree_canopy_source=tree_canopy_source,
                recategorize=recategorize_mode
            )
