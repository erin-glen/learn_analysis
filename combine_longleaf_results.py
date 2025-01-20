# combine_longleaf_results.py

import os
import re
import pandas as pd

def combine_longleaf_results(base_dir, output_csv):
    """
    Scans 'base_dir' for subfolders containing 'longleaf_combined_ghg.csv',
    extracts the inventory period (e.g., 2019_2021) from the folder name,
    and merges these CSVs into a single file with added 'Year1' and 'Year2' columns.

    Example folder name:
        2025_01_15_2019_2021_LongleafAnalysis

    Regex Explanation:
    - We look for the pattern: _(4 digits)_(4 digits)_
      e.g. "_2019_2021_"
    - If found, those become Year1='2019' and Year2='2021'

    Args:
        base_dir (str): Path to the parent directory containing all inventory output folders.
        output_csv (str): Full path (including filename) for the final combined CSV.
    """
    if not os.path.exists(base_dir):
        print(f"Base directory does not exist: {base_dir}")
        return

    subfolders = [
        f for f in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, f))
    ]

    all_dataframes = []

    for folder_name in subfolders:
        folder_path = os.path.join(base_dir, folder_name)

        # Regex to extract year1, year2 from something like "_2019_2021_"
        match = re.search(r'_(\d{4})_(\d{4})_', folder_name)
        if match:
            year1, year2 = match.groups()  # e.g. ('2019', '2021')

            csv_path = os.path.join(folder_path, "longleaf_combined_ghg.csv")
            if os.path.exists(csv_path):
                try:
                    df = pd.read_csv(csv_path)
                    # Add columns for the inventory period
                    df["Year1"] = year1
                    df["Year2"] = year2
                    all_dataframes.append(df)
                    print(f"Appended data from: {csv_path}")
                except Exception as e:
                    print(f"Error reading {csv_path}: {e}")
            else:
                print(f"No 'longleaf_combined_ghg.csv' in {folder_path}")
        else:
            print(f"Folder '{folder_name}' does not match the year pattern. Skipping.")

    # Combine all dataframes and save to output_csv
    if all_dataframes:
        combined_df = pd.concat(all_dataframes, ignore_index=True)
        combined_df.to_csv(output_csv, index=False)
        print(f"\nCombined {len(all_dataframes)} file(s). Results saved to:")
        print(f"  {output_csv}")
    else:
        print("\nNo valid CSV files were found to combine.")

if __name__ == "__main__":
    # Example usage:
    # Adjust 'base_directory' to the path where all your "2025_01_15_2019_2021_LongleafAnalysis"
    # folders reside, and set your desired output CSV name.
    base_directory = r"C:\GIS\Data\LEARN\Outputs"
    output_file = os.path.join(base_directory, "all_longleaf_ghg_results.csv")

    combine_longleaf_results(base_directory, output_file)
