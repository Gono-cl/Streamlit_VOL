"""Shared campaign catalogs used by optimization pages."""

VARIABLE_OPTIONS = {
    "Temperature": "temperature",
    "Pressure": "pressure",
    "Ratio oraganic/aqueous": "ratio_org_aq",
    "Acid": "acid",
    "Residence Time": "residence_time",
    "Flow Rate": "flow_rate",
    "base Concentration": "base_concentration",
    "Voltage": "Voltage",
    "Total Current": "total_current",
    "Current Density": "current_density",
    "Applied Charge": "applied_charge",
    "Substrate Concentration": "substrate_concentration",
    "Acid Concentration": "acid_concentration",
    "Salt concentration" : "salt_concentration"
}

SINGLE_OBJECTIVE_OPTIONS = [
    "Yield",
    "Area",
    "Concentration",
    "Throughput",
    "Used Organic",
    "Solvent Penalty",
    "Extraction Efficiency",
    "Space-Time Yield",
]

MULTI_OBJECTIVE_OPTIONS = [
    "Yield",
    "Normalized Area",
    "Concentration",
    "Throughput",
    "Used Organic",
    "Solvent Penalty",
    "Extraction Efficiency",
    "Space-Time Yield",
]

INIT_STRATEGY_OPTIONS = ["Random", "LHS"]
OPTIMIZER_ACQ_OPTIONS = ["EI", "PI", "LCB", "gp_hedge"]
