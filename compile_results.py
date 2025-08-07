import os
import glob
import pandas as pd

# Hardcoded input and output paths
input_dir = r"C:\GIS\LEARN\Outputs\2025_04_28_18_42_us_places"
output_path = r"C:\GIS\LEARN\Outputs\us_places.csv"


def combine_csvs(input_dir, output_path):
    # Find all CSV files in the directory
    csv_files = glob.glob(os.path.join(input_dir, "*.csv"))

    if not csv_files:
        print(f"No CSV files found in {input_dir}")
        return

    print(f"Found {len(csv_files)} CSV files. Combining...")

    # Read and combine all CSVs, ensuring FeatureID is read as string
    combined_df = pd.concat(
        (pd.read_csv(f, dtype={'FeatureID': str}) for f in csv_files),
        ignore_index=True
    )

    # Save the result, ensuring FeatureID stays as string
    combined_df.to_csv(output_path, index=False)
    print(f"Combined CSV written to: {output_path}")


# Execute the function
combine_csvs(input_dir, output_path)