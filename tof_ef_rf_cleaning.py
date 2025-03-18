import os
import pandas as pd
import geopandas as gpd

def clean_string(s):
    """Generic cleaning function: strip, lowercase, handle non-string safely."""
    if not isinstance(s, str):
        return ""
    return s.strip().lower()

def main():
    print("============================================================")
    print("STEP 1. READ tof_ef.csv (EMISSION FACTORS, PLACE/STATE)")
    print("============================================================")
    ef_csv_path = r"C:\GIS\Data\LEARN\SourceData\TOF\tof_ef.csv"
    ef_df = pd.read_csv(ef_csv_path)
    print(f"Read {len(ef_df)} rows from {ef_csv_path}")
    print("Columns:", list(ef_df.columns))
    print("Sample ef_df rows:")
    print(ef_df.head(5))
    print()

    print("============================================================")
    print("STEP 2. READ tof_rf.csv (REMOVAL FACTORS, STATE ONLY)")
    print("============================================================")
    rf_csv_path = r"C:\GIS\Data\LEARN\SourceData\TOF\tof_rf.csv"
    rf_df = pd.read_csv(rf_csv_path)
    print(f"Read {len(rf_df)} rows from {rf_csv_path}")
    print("Columns:", list(rf_df.columns))
    print("Sample rf_df rows:")
    print(rf_df.head(5))
    print()

    print("============================================================")
    print("STEP 3. READ SHAPEFILES (STATES & PLACES)")
    print("============================================================")
    states_shp = r"C:\GIS\Data\LEARN\census\tl_2024_us_state\tl_2024_us_state.shp"
    places_shp = r"C:\GIS\Data\LEARN\census\tl_2023_us_place\tl_2023_us_place\tl_2023_us_place.shp"

    states_gdf = gpd.read_file(states_shp)
    places_gdf = gpd.read_file(places_shp)

    print(f"States shapefile has {len(states_gdf)} records")
    print("Sample states_gdf:")
    print(states_gdf.head(2))
    print()
    print(f"Places shapefile has {len(places_gdf)} records")
    print("Sample places_gdf:")
    print(places_gdf.head(2))
    print()

    # -------------------------------------------------------------
    # Build a STUSPS -> STATEFP lookup
    # -------------------------------------------------------------
    print("Building STUSPS->STATEFP lookup for place merges ...")
    st_abbrev_df = (
        states_gdf[['STUSPS', 'STATEFP']]
        .drop_duplicates()
        .dropna(subset=['STUSPS','STATEFP'])
        .copy()
    )
    st_abbrev_df['STUSPS_clean'] = st_abbrev_df['STUSPS'].str.upper().str.strip()
    st_abbrev_df['STATEFP_clean'] = st_abbrev_df['STATEFP'].str.strip()

    st_abbrev_to_fips = dict(
        zip(st_abbrev_df['STUSPS_clean'], st_abbrev_df['STATEFP_clean'])
    )
    print(f"Found {len(st_abbrev_to_fips)} unique STUSPS->STATEFP mappings.")
    print()

    # -------------------------------------------------------------
    # STEP 4. Process tof_ef.csv: Split by Type => "place" vs "state"
    # -------------------------------------------------------------
    print("Splitting tof_ef into place-rows vs. state-rows ...")
    ef_df['Type_clean'] = ef_df['Type'].str.lower().str.strip()
    ef_places_df = ef_df[ef_df['Type_clean'] == 'place'].copy()
    ef_states_df = ef_df[ef_df['Type_clean'] == 'state'].copy()

    print(f" - Found {len(ef_places_df)} 'place' rows.")
    print(f" - Found {len(ef_states_df)} 'state' rows.")
    print()

    # -------------------------------------------------------------
    # STEP 5a: FIRST PASS - MERGE PLACES BY NAME + STATEFP
    # -------------------------------------------------------------
    print("============================================================")
    print("FIRST PASS - MERGE PLACES by (city_clean, STATEFP)")
    print("============================================================")

    # 1) Create 'city_clean' and 'state_abbrev'
    ef_places_df['state_abbrev'] = ef_places_df['State'].str.upper().str.strip()
    ef_places_df['STATEFP'] = ef_places_df['state_abbrev'].map(st_abbrev_to_fips)
    ef_places_df['city_clean'] = ef_places_df['Place'].apply(clean_string)

    # 2) Clean place shapefile to match city names
    places_gdf['NAME_clean'] = places_gdf['NAME'].apply(clean_string)

    # 3) Merge on (city_clean, STATEFP)
    temp_places = pd.merge(
        ef_places_df,
        places_gdf[['NAME_clean','STATEFP']],
        left_on=['city_clean','STATEFP'],
        right_on=['NAME_clean','STATEFP'],
        how='left',
        indicator=True
    )
    unmatched_places = temp_places[temp_places['_merge']=='left_only'].copy()

    # 4) The "matched" subset
    matched_places = temp_places[temp_places['_merge']=='both'].copy()

    print(f"First-pass found {len(matched_places)} matched rows, {len(unmatched_places)} unmatched rows.")
    print()

    # 5) We'll do the geometry join for the matched subset:
    matched_places_merged = places_gdf.merge(
        matched_places.drop(columns=['_merge','NAME_clean']),  # drop merge indicator/duplicate col
        left_on=['NAME_clean','STATEFP'],
        right_on=['city_clean','STATEFP'],
        how='inner'
    )
    print(f"After geometry merge: matched_places_merged has {len(matched_places_merged)} rows.")
    print()

    # -------------------------------------------------------------
    # STEP 5a.1: IDENTIFY UNMATCHED ROWS => APPLY OVERRIDES
    # -------------------------------------------------------------
    print("============================================================")
    print("APPLY OVERRIDES TO UNMATCHED ROWS")
    print("============================================================")

    # We'll create a small dictionary of overrides to fix certain city->PLACEFP
    # Key: (city_clean, state_abbrev)
    # Value: (STATEFP, PLACEFP)
    place_overrides = {
        ("jerseycity",   "NJ"): ("34", "36000"),
        ("moorestown",   "NJ"): ("34", "47895"),
        ("newyork",      "NY"): ("36", "51000"),
        ("grandrapids",  "MI"): ("26", "34000"),
        ("elpaso",       "TX"): ("48", "24000"),
        ("boise",        "IDAHO"):     ("16", "08830"),
        ("albuquerque",  "NEWMEXICO"): ("35", "02000"),
        ("casper",       "WYOMING"):   ("56", "13150"),
        ("golden",       "COLORADO"):  ("08", "30835"),
        ("lascruces",    "NEWMEXICO"): ("35", "39380"),
        ("phoenix",      "ARIZONA"):   ("04", "55000"),
        ("losangeles",   "CA"): ("06", "44000"),
        ("sanfrancisco", "CA"): ("06", "67000"),
        ("lakeforestpark","WA"):("53", "37270"),
        # If you have more overrides for other states (e.g., "New Mexico" vs "NEWMEXICO"), adjust as needed.
    }

    # Keep only the unmatched
    unmatched_df = unmatched_places.drop(columns=['_merge','NAME_clean']).copy()

    # Because unmatched_df might have missing STATEFP if 'state_abbrev' was not recognized,
    # let's ensure we at least fill it in from st_abbrev_to_fips:
    mask_missing = unmatched_df['STATEFP'].isna()
    unmatched_df.loc[mask_missing, 'STATEFP'] = (
        unmatched_df.loc[mask_missing, 'state_abbrev'].map(st_abbrev_to_fips)
    )

    # Function to apply overrides if a (city_clean, state_abbrev) is found
    def apply_place_overrides(row):
        key = (row['city_clean'], row['state_abbrev'])
        if key in place_overrides:
            override_statefp, override_placefp = place_overrides[key]
            row['STATEFP'] = override_statefp
            row['PLACEFP'] = override_placefp
        return row

    unmatched_df = unmatched_df.apply(apply_place_overrides, axis=1)

    print(f"Unmatched rows after applying overrides: {len(unmatched_df)} total rows remain. Some will have PLACEFP now.")
    print(unmatched_df.head(10))
    print()

    # -------------------------------------------------------------
    # STEP 5a.2: SECOND PASS - MERGE OVERRIDE-FIXED ROWS
    # -------------------------------------------------------------
    print("============================================================")
    print("SECOND PASS - MERGE OVERRIDE-FIXED ROWS by (STATEFP, PLACEFP)")
    print("============================================================")

    # We'll only merge rows that actually have a PLACEFP
    has_placefp_mask = unmatched_df['PLACEFP'].notna()
    unmatched_with_placefp = unmatched_df[has_placefp_mask].copy()
    print(f"Of {len(unmatched_df)} unmatched rows, {len(unmatched_with_placefp)} now have a PLACEFP from overrides.")
    print()

    # Merge them on [STATEFP, PLACEFP]
    second_pass_merged = places_gdf.merge(
        unmatched_with_placefp,
        on=['STATEFP','PLACEFP'],
        how='inner'
    )
    print(f"Second-pass override merge yields {len(second_pass_merged)} additional rows.")
    print()

    # Combine first-pass matched rows + second-pass matched rows
    places_merged_final = pd.concat([matched_places_merged, second_pass_merged], ignore_index=True)
    places_merged_final = gpd.GeoDataFrame(places_merged_final, geometry='geometry')

    print(f"TOTAL place-based matches after name-based + overrides = {len(places_merged_final)}")
    print("Sample final columns:", list(places_merged_final.columns))
    print(places_merged_final.head(3))
    print()

    # We'll rename the final merged geodataframe to "places_merged" for consistent naming
    places_merged = places_merged_final

    # -------------------------------------------------------------
    # STEP 5b: MERGE STATES (EMISSIONS)
    # -------------------------------------------------------------
    print("============================================================")
    print("MERGE STATES: Emission Factors, STUSPS code")
    print("============================================================")
    ef_states_df['state_abbrev'] = ef_states_df['State'].str.upper().str.strip()

    # Clean states shapefile for STUSPS
    states_gdf['STUSPS_clean'] = states_gdf['STUSPS'].str.upper().str.strip()

    print("Pre-merge QA: Checking for unmatched state-rows (ef_states_df vs states_gdf) ...")
    temp_states = pd.merge(
        ef_states_df,
        states_gdf[['STUSPS_clean']],
        left_on='state_abbrev',
        right_on='STUSPS_clean',
        how='left',
        indicator=True
    )
    unmatched_states = temp_states[temp_states['_merge']=='left_only'].copy()
    if len(unmatched_states)>0:
        print(f"WARNING: {len(unmatched_states)} state-rows did not match STUSPS in states shapefile.")
        print(unmatched_states[['Place','State','tof_ef','state_abbrev']])
    else:
        print("All ef state-rows matched!")
    print()

    # Actual "inner" merge
    ef_states_merged = states_gdf.merge(
        ef_states_df,
        left_on='STUSPS_clean',
        right_on='state_abbrev',
        how='inner'
    )
    print(f"Final ef_states_merged has {len(ef_states_merged)} rows.")
    print(ef_states_merged.head(3))
    print()

    # -------------------------------------------------------------
    # STEP 6. PROCESS tof_rf.csv: STATE REMOVAL FACTORS
    # -------------------------------------------------------------
    print("============================================================")
    print("MERGE STATES: Removal Factors (tof_rf) by State Name")
    print("============================================================")
    states_gdf['NAME_clean'] = states_gdf['NAME'].apply(clean_string)
    rf_df['state_clean'] = rf_df['State'].apply(clean_string)

    print("Pre-merge QA: Checking for unmatched states in rf_df vs. states_gdf NAME...")
    temp_rf = pd.merge(
        rf_df,
        states_gdf[['NAME_clean']],
        left_on='state_clean',
        right_on='NAME_clean',
        how='left',
        indicator=True
    )
    unmatched_rf = temp_rf[temp_rf['_merge']=='left_only'].copy()
    if len(unmatched_rf)>0:
        print(f"WARNING: {len(unmatched_rf)} removal-factor state-rows did not match any state name.")
        print(unmatched_rf[['State','tof_rf','state_clean']])
    else:
        print("All removal-factor states matched!")
    print()

    # Actual "inner" merge for the removal factors
    rf_states_merged = states_gdf.merge(
        rf_df,
        left_on='NAME_clean',
        right_on='state_clean',
        how='inner'
    )
    print(f"Final rf_states_merged has {len(rf_states_merged)} rows.")
    print(rf_states_merged.head(3))
    print()

    # -------------------------------------------------------------
    # STEP 7. WRITE OUT SHAPEFILES (Separate)
    # -------------------------------------------------------------
    out_dir = r"C:\GIS\Data\LEARN\SourceData\TOF\usca_factors"
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    print("============================================================")
    print("WRITING OUTPUT SHAPEFILES ...")
    print("============================================================")

    # 1) Places with Emission Factors
    places_out = os.path.join(out_dir, "tof_ef_places.shp")
    print(f"Writing {len(places_merged)} place-based EF records to {places_out}")
    places_merged.to_file(places_out, driver="ESRI Shapefile")

    # 2) States with Emission Factors
    ef_states_out = os.path.join(out_dir, "tof_ef_states.shp")
    print(f"Writing {len(ef_states_merged)} state-based EF records to {ef_states_out}")
    ef_states_merged.to_file(ef_states_out, driver="ESRI Shapefile")

    # 3) States with Removal Factors
    rf_states_out = os.path.join(out_dir, "tof_rf_states.shp")
    print(f"Writing {len(rf_states_merged)} state-based RF records to {rf_states_out}")
    rf_states_merged.to_file(rf_states_out, driver="ESRI Shapefile")

    # -------------------------------------------------------------
    # STEP 8. COMBINE PLACE + STATE EMISSION FACTORS INTO "tof_ef.shp"
    # -------------------------------------------------------------
    print("\n============================================================")
    print("STEP 8. COMBINE PLACES + STATES EMISSION FACTORS")
    print("============================================================")

    # We'll concat the two GeoDataFrames
    combined_ef = pd.concat([places_merged, ef_states_merged], ignore_index=True)
    combined_ef = gpd.GeoDataFrame(combined_ef, geometry='geometry')

    # Write to a single shapefile
    ef_combined_out = os.path.join(out_dir, "tof_ef.shp")
    print(f"Writing {len(combined_ef)} combined EF records to {ef_combined_out}")
    combined_ef.to_file(ef_combined_out, driver="ESRI Shapefile")

    print()
    print("Done! Wrote shapefiles:")
    print("  ", places_out)
    print("  ", ef_states_out)
    print("  ", rf_states_out)
    print("  ", ef_combined_out)
    print()
    print("Now you have a single 'tof_ef.shp' with BOTH places and states for emission factors.")
    print("Likewise, 'rf_states_merged' is separate for the removal factors. Exiting now.")

if __name__ == "__main__":
    main()
