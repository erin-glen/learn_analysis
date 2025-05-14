# Land Emissions and Removals Navigator (LEARN) Analysis Toolkit

This repository contains the code and methodology supporting the publication:

> **Glen, E., Scafidi, A., Harris, N., & Birdsey, R. (In Review). Support for Subnational Entities to Develop and Monitor Land-based Greenhouse Gas Reduction Activities. Forests.**

## Purpose

This toolkit facilitates detailed analyses of forest carbon dynamics, land-use transitions, and community impacts, aligning with guidelines from USDA, IPCC, and the U.S. Community Protocol. It provides a standardized framework to support subnational greenhouse gas (GHG) inventories as described in our publication.

## Repository Branches

This repository uses multiple branches to manage different stages of development:

* **`main`**: Stable, documented codebase reflecting methods used in the publication.
* **`development`**: Active branch for ongoing enhancements and new features.
* **`analyses/XYZ`**: Feature-specific branches for methodological experiments or specific analyses.

## Main Branch Structure

The following structure applies specifically to the **`main`** branch:

```bash
learn_analysis/
├── forests_analysis.py          # Forest carbon accounting script
├── communities_analysis.py      # Community-level impact analysis script
├── analysis_core.py             # Core analytical functions and geoprocessing logic
├── config.py                    # Centralized configuration settings (paths, constants)
├── funcs.py                     # General-purpose utility functions
├── lookups.py                   # Lookup tables used in classifications
└── outputs/                     # Analysis outputs and results
```

## Methodological Details

Detailed methodology, including activity data preparation, emissions and removals calculation, and integration of geospatial datasets, are comprehensively described in Sections 2.3 and 2.4 of our publication.

### Data Sources

| **Variable / Category** | **Dataset** | **Resolution** | **Years Available in Tool** |
|-------------------------|-------------|----------------|-----------------------------|
| **Land Cover** | | | |
| Land Cover | National Land Cover Database (NLCD) | 30 m | 2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021, 2023 |
| **Tree Canopy** | | | |
| Tree Canopy Cover | National Land Cover Database (NLCD) | 30 m | 2011, 2013, 2016, 2019, 2021, 2023 |
| **Forest Disturbances** | | | |
| Forest Fires | Monitoring Trends in Burn Severity (MTBS) | 30 m | 2001–2023 (annual) |
| Insect & Disease | Insect and Disease Detection Survey | Varies | 2001–2023 (annual) |
| Timber Harvest & Other | Global Forest Watch Tree Cover Loss | 30 m | 2001–2023 (annual) |
| **Estimating Emission and Removal Factors – Removal Factors** | | | |
| Forest Type | FIA Forest Type Groups | 30 m | Single estimate (2014–2018) |
| Plantations | Spatial Database of Planted Trees (v1) | Varies | 2015 |
| Forest Age | FIA Forest Stand Age | 30 m | Single estimate (2014–2018) |
| Undisturbed Forests | FIA Database | Non-spatial | Varies by region/variables |
| Afforestation / Reforestation | FIA Database | Non-spatial | Varies by region/variables |
| Trees Outside Forests (Removal) | Urban Trees Emission & Removal Factors | Non-spatial | 2005 |
| **Estimating Emission and Removal Factors – Emission Factors** | | | |
| Carbon Stocks | BIGMAP Forest Carbon Pools | 30 m | Single estimate (2014–2018) |
| Trees Outside Forests (Emission) | Urban Trees Emission & Removal Factors | Non-spatial | 2005 |
| Forest Disturbances | Regionally Modeled Disturbance Database | Non-spatial | Derived from FIA data (2001–2010) |




## Contributing and Maintenance

We encourage contributions and feedback through GitHub Issues and pull requests. 

## Citation and License

If using this code for academic purposes, please cite:

> Glen, E., Scafidi, A., Harris, N., & Birdsey, R. (2025). Support for Subnational Entities to Develop and Monitor Land-based Greenhouse Gas Reduction Activities. *Forests, 2025.*

This project is licensed under the MIT License.

## Acknowledgments

We are pleased to acknowledge the many organizations and individuals who have contributed to this work. We extend our gratitude to ICLEI-USA for leading community engagement, organizing training cohorts, and hosting the LEARN Tool on their website. Susan 
Minnemeyer and the Chesapeake Conservancy have been instrumental in piloting the use of high-resolution tree canopy data and organizing communities in the Chesapeake BayWatershed. Barry (Ty) Wilson (USDA FS) has provided endless support in utilizing the BIGMAP data products. Andrew 
Lister (USDA FS) has helped to guide data and user interface priorities through constructive review and advising. We are appreciative of Garrett Rose and Carolyn Ramirez from the National Resources Defense Council for their collaboration and interest in assessing federally owned forests. We thank 
Donna Lee for her work on the LEARN tool’s conceptualization and early pilot testing. We are indebted to the various communities and organizations that actively participated in the piloting of the LEARN Tool. Finally, we thank our GIS and web development partners, Blue Raster, for their contribution to creating and maintaining the LEARN platform. Eric Ashcroft (Blue Raster) has been instrumental in leading technical development and facilitating strategic engagement. 