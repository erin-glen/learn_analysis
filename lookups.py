# dictionary lookups

nlcdCategories = {
	11: 'Open Water',
	12: 'Perennial Ice/Snow',
	21: 'Developed, Open Space',
	22: 'Developed, Low Intensity',
	23: 'Developed, Medium Intensity',
	24: 'Developed, High Intensity',
	31: 'Barren Land',
	41: 'Deciduous Forest',
	42: 'Evergreen Forest',
	43: 'Mixed Forest',
	51: 'Dwarf Scrub',
	52: 'Shrub/Scrub',
	71: 'Herbaceous',
	72: 'Sedge/Herbaceous',
	73: 'Lichens',
	74: 'Moss',
	81: 'Hay/Pasture',
	82: 'Cultivated Crops',
	90: 'Woody Wetlands',
	95: 'Emergent Herbaceous Wetlands'
}


nlcdParentRollupCategories = {
	'Open Water': 'Wetland',
	'Perennial Ice/Snow': 'Other Land',
	'Developed, Open Space': 'Settlement',
	'Developed, Low Intensity': 'Settlement',
	'Developed, Medium Intensity': 'Settlement',
	'Developed, High Intensity': 'Settlement',
	'Barren Land': 'Other Land',
	'Deciduous Forest': 'Forestland',
	'Evergreen Forest': 'Forestland',
	'Mixed Forest': 'Forestland',
	'Dwarf Scrub': 'None',
	'Shrub/Scrub': 'Grassland',
	'Herbaceous': 'Grassland',
	'Sedge/Herbaceous': 'None',
	'Lichens': 'None',
	'Moss': 'None',
	'Hay/Pasture': 'Grassland',
	'Cultivated Crops': 'Cropland',
	'Woody Wetlands': 'Forestland',
	'Emergent Herbaceous Wetlands': 'Wetland'
}


disturbanceLookup = {
	#0: 'no_disturbance_HA',
	1: 'harvest_HA',
	5: 'insect_damage_HA',
	10: 'fire_HA'
}

carbonStockLoss = {
    "Cropland": {
        "biomass": 1,
        "dead organic matter": 1,
        "soil organic": 0.23,
    },
    "Grassland": {
        "biomass": 0.5,
        "dead organic matter": 1,
        "soil organic": 0,
    },
    "Wetland": {
        "biomass": 1,
        "dead organic matter": 1,
        "soil organic": 0,
    },
    "Settlement": {
        "biomass": 1,
        "dead organic matter": 1,
        "soil organic": 0.3,
    },
    "Other Land": {
        "biomass": 1,
        "dead organic matter": 1,
        "soil organic": 1,
    }
}
