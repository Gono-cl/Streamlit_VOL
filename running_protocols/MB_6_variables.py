from __future__ import annotations

import csv
import math
import time
from datetime import datetime
from pathlib import Path


# ============================================================
# CONSTANTS
# ============================================================

FARADAY_CONSTANT = 96485.33212  # C/mol

# Methylene blue chloride, anhydrous.
# Change if the actual reagent is a hydrate or another form.
MB_MW_G_MOL = 319.85

# Pump 1 concentrated MB stock
MB_STOCK_CONCENTRATION_MG_L = 2000.0

# Minimum desired total reaction flow
MIN_TOTAL_FLOW_ML_MIN = 0.45


# ============================================================
# HYDRAULIC TIMING
# ============================================================

# Number of cell volumes used to ensure the selected cell
# contains the new formulation before electrochemistry begins.
PRE_ECHEM_CELL_VOLUMES = 2.0

# Number of electrochemical cell residence times allowed
# after current is switched on before considering the reactor
# response established.
STEADY_STATE_CELL_RESIDENCE_TIMES = 3.0

# TODO:
# Measure tubing/mixer volume between the mixing point
# and the inlet of the electrochemical cell.
UPSTREAM_DEAD_VOLUME_ML = 0.0

# TODO:
# Measure total volume between electrochemical cell outlet
# and UV/Vis measurement point:
#
# outlet tubing
# + gas/liquid separator
# + tubing to UV/Vis
# + relevant flow cell volume if desired
DOWNSTREAM_DEAD_VOLUME_ML = 0.0


# ============================================================
# CELL METADATA
# ============================================================

# cell_port is shared by ROT_VALVE (inlet) and the external AMF valve (outlet).
CELL_METADATA = {
    "EC1": {
        "structure": "fin",
        "area_cm2": 4.74,
        "volume_uL": 120.54,
        "gap_mm": 0.254,
        "anode": "graphite",
        "cathode": "stainless steel",
        "bkk_channel": 1,
        "cell_port": 1,
    },

    "EC2": {
        "structure": "meandering",
        "area_cm2": 6.31,
        "volume_uL": 160.38,
        "gap_mm": 0.254,
        "anode": "graphite",
        "cathode": "stainless steel",
        "bkk_channel": 2,
        "cell_port": 2,
    },

    "EC3": {
        "structure": "tangential",
        "area_cm2": 4.85,
        "volume_uL": 123.38,
        "gap_mm": 0.254,
        "anode": "graphite",
        "cathode": "stainless steel",
        "bkk_channel": 3,
        "cell_port": 3,
    },
}


# ============================================================
# ELECTROLYTE METADATA
# ============================================================

# SPM+ internal valve:
#
# port 1 -> waste (homing always expels through port 1)
# port 2 -> Na2SO4
# port 3 -> NaCl
# port 4 -> NaNO3
# port 5 -> reactor delivery
#
# Each new or resumed campaign homes the full SPM through waste port 1 once,
# then rinses with the incoming electrolyte. Later experiments retain/top up
# matching stock.
# Z/Y homing always uses port 1; a prior valve selection cannot redirect it.
#
# Current stock concentration = 0.4 M

ELECTROLYTE_METADATA = {
    "Na2SO4": {
        "spm_port": 2,
        "stock_concentration_M": 0.4,
    },

    "NaCl": {
        "spm_port": 3,
        "stock_concentration_M": 0.4,
    },

    "NaNO3": {
        "spm_port": 4,
        "stock_concentration_M": 0.4,
    },
}

SPM_DELIVERY_PORT = 5
SPM_WASTE_PORT = 1


# ============================================================
# SPM+ SETTINGS
# ============================================================

# IMPORTANT:
# SPMPump class uses uL and uL/min.

SPM_LOADING_FLOW_UL_MIN = 5000.0

# Full 5 mL syringe
SPM_LOAD_VOLUME_UL = 5000.0

# On a change of stock solution, or when the contents are unknown, discard
# the residual volume and rinse twice with 1 mL of the incoming electrolyte.
# These values may also be overridden with spm_* keys in the adapter config.
SPM_RINSE_VOLUME_UL = 1000.0
SPM_RINSE_CYCLES = 2
SPM_DISCARD_FLOW_UL_MIN = 5000.0

# Minimum practical flow of SPM+
SPM_MIN_FLOW_UL_MIN = 50.0


# ============================================================
# HITECH ZANG PUMPS
# ============================================================

# Pump 1 = MB stock
# Pump 2 = dilution water
# Pump 3 = downstream analytical dilution water

PUMP_MB_TAG = (
    "Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1"
)

PUMP_WATER_TAG = (
    "Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1"
)

PUMP_ANALYTICAL_DILUTION_TAG = (
    "Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1"
)
ANALYTICAL_DILUTION_MAX_FLOW_ML_MIN = 6.25
ANALYTICAL_DILUTION_WAIT_S = 10.0
ANALYTICAL_DILUTION_MAX_ATTEMPTS = 6


def uvvis_dilution_settings(runner, parameters):
    """Real UV/VIS experiments start directly, then dilute only if above range."""
    op = _compute_operating_point(parameters)
    saved = dict(getattr(runner, "uvvis_config", {}).get("auto_dilution", {}) or {})
    config = getattr(runner, "adapter_config", {}) or {}
    maximum = config.get("uvvis_dilution_max_flow_ml_min", saved.get(
        "max_water_flow_ml_min", ANALYTICAL_DILUTION_MAX_FLOW_ML_MIN))
    if isinstance(maximum, bool):
        raise ValueError("MB Pump 3 dilution limit must be a number in mL/min.")
    maximum = float(maximum)
    if not math.isfinite(maximum) or not 0 < maximum <= ANALYTICAL_DILUTION_MAX_FLOW_ML_MIN:
        raise ValueError("MB Pump 3 dilution limit must be positive and no greater than 6.25 mL/min.")
    return {
        "reaction_flow_ml_min": op["total_flow_ml_min"],
        "inlet_concentration_mg_L": op["mb_target_mg_L"],
        "max_water_flow_ml_min": maximum,
        "settle_time_s": config.get("uvvis_dilution_wait_s", saved.get(
            "settle_time_s", ANALYTICAL_DILUTION_WAIT_S)),
        "max_attempts": config.get("uvvis_dilution_max_attempts", saved.get(
            "max_attempts", ANALYTICAL_DILUTION_MAX_ATTEMPTS)),
    }


def set_uvvis_dilution_flow(runner, flow_ml_min):
    """Write/read back Pump 3 in uL/min; return its verified command in mL/min."""
    if runner.simulation_mode != "off" or runner.measurement_backend != "uvvis":
        raise RuntimeError("Automatic UV/VIS dilution is only available in real UV/VIS experiments.")
    flow = float(flow_ml_min)
    maximum = float(runner.uvvis_config.get("auto_dilution", {}).get(
        "max_water_flow_ml_min", ANALYTICAL_DILUTION_MAX_FLOW_ML_MIN))
    if not math.isfinite(flow) or not 0 <= flow <= min(maximum, ANALYTICAL_DILUTION_MAX_FLOW_ML_MIN):
        raise ValueError("Pump 3 dilution flow exceeds the configured 6.25 mL/min maximum.")
    command = min(maximum * 1000.0, round(flow * 1000.0, 4))
    _required_write(runner, PUMP_ANALYTICAL_DILUTION_TAG, command)
    _verify_opc_values(runner, ((PUMP_ANALYTICAL_DILUTION_TAG, command),), "analytical dilution flow")
    runner._mb_analytical_dilution_flow_ml_min = command / 1000.0
    return command / 1000.0

# Pump 6 uses mL/min (unlike the reagent pumps above, which use uL/min).
PUMP_CLEANING_TAG = "Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1"
# ROT_VALVE is now the cell INLET selector. The external USB AMF valve is
# the matching OUTLET selector. Both use ports 1/2/3 for EC1/EC2/EC3.
# Pump 6 has a direct water supply and does not select a cleaning-liquid port.
CELL_INLET_VALVE_TAG = "Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS"
CLEANING_FLOW_ML_MIN = 1.0
CLEANING_DURATION_S = 60.0

# Rebuilt MB routing: electrolysis V_02=0/V_01=1; cleaning V_02=1/V_01=0.
# Readbacks below confirm OPC command values, not mechanical position sensors.
VALVE_ELECTROLYSIS_TAGS = (
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 1),
)
VALVE_CLEANING_TAGS = tuple((tag, 1 - value) for tag, value in VALVE_ELECTROLYSIS_TAGS)
POWER_OFF_TAGS = (
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTON", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTOFF", 1),
)
FEED_STOP_TAGS = tuple((tag, 0) for tag in (
    PUMP_MB_TAG, PUMP_WATER_TAG, PUMP_ANALYTICAL_DILUTION_TAG,
))
OPC_CONTROL_TIMEOUT_S = 2.0
OPC_CHECK_ATTEMPTS = 3
OPC_CHECK_INTERVAL_S = 0.25


def _required_write(runner, tag, value):
    if runner.opc.write_value(tag, value, timeout=OPC_CONTROL_TIMEOUT_S) is False:
        raise RuntimeError(f"Required OPC write failed: {tag} = {value}.")


def _verify_opc_values(runner, expected, description):
    """Require readable command values before continuing to the next phase."""
    for attempt in range(OPC_CHECK_ATTEMPTS):
        mismatches = []
        for tag, target in expected:
            try:
                value = runner.opc.read_value(tag, timeout=OPC_CONTROL_TIMEOUT_S)
                number = float(value.strip().replace(",", ".")) if isinstance(value, str) else float(value)
                if not math.isfinite(number) or not math.isclose(number, target, abs_tol=1e-6):
                    mismatches.append(f"{tag}: expected {target}, got {value!r}")
            except Exception as exc:
                mismatches.append(f"{tag}: readback unavailable ({exc})")
        if not mismatches:
            return
        if attempt + 1 < OPC_CHECK_ATTEMPTS:
            time.sleep(OPC_CHECK_INTERVAL_S)
    raise RuntimeError(f"Could not verify {description}: " + "; ".join(mismatches))


def _ensure_power_off(runner):
    runner.turn_off_power_supply()
    _verify_opc_values(runner, POWER_OFF_TAGS, "power-supply OFF commands")


def _set_flow_mode(runner, mode):
    if mode not in ("electrolysis", "cleaning"):
        raise ValueError(f"Unknown flow mode: {mode!r}.")
    commands = VALVE_ELECTROLYSIS_TAGS if mode == "electrolysis" else VALVE_CLEANING_TAGS
    for tag, value in commands:
        _required_write(runner, tag, value)
    _verify_opc_values(runner, commands, f"{mode} valve commands")
    print(f"Switching valves: {mode} mode commands verified.")


def _stop_cleaning_pump(runner):
    _required_write(runner, PUMP_CLEANING_TAG, 0)
    _verify_opc_values(runner, ((PUMP_CLEANING_TAG, 0),), "cleaning pump stopped")
    runner._mb_cleaning_active = False


def _stop_reagent_feeds(runner):
    errors = []
    for tag, value in FEED_STOP_TAGS:
        try:
            _required_write(runner, tag, value)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("Could not stop all reagent feeds: " + "; ".join(map(str, errors))) from errors[0]
    _verify_opc_values(runner, FEED_STOP_TAGS, "reagent feeds stopped")


# ============================================================
# BKK CHANNEL TAGS
# ============================================================

BKK_CHANNEL_TAGS = {
    1: "Hitec_OPC_DA20_Server->E_CHEM:BKK_USER_DEFINE.CHANNEL1",
    2: "Hitec_OPC_DA20_Server->E_CHEM:BKK_USER_DEFINE.CHANNEL2",
    3: "Hitec_OPC_DA20_Server->E_CHEM:BKK_USER_DEFINE.CHANNEL3",
}


# ============================================================
# LOGGING
# ============================================================

LOG_FILE = Path("MB_experiment_log.csv")


# ============================================================
# BO PARAMETERS
# ============================================================

def required_parameter_keys():
    return [
        "current_density",
        "applied_charge",
        "substrate_concentration",
        "electrolyte_concentration",
        "cell",
        "electrolyte",
    ]


# ============================================================
# OPERATING POINT CALCULATION
# ============================================================

def _compute_operating_point(parameters):
    """
    Convert one BO proposal into executable process conditions.

    BO variables
    ------------
    current_density
        mA/cm2

    applied_charge
        F/mol

    substrate_concentration
        Final MB concentration at cell inlet, mg/L

    electrolyte_concentration
        Final electrolyte concentration at cell inlet, mol/L

    cell
        EC1 / EC2 / EC3

    electrolyte
        Na2SO4 / NaCl / NaNO3


    Reaction feed architecture
    --------------------------
    Pump 1 = 2000 mg/L MB stock
    Pump 2 = dilution water
    SPM+   = 0.4 M selected electrolyte stock

    Pump 3 is downstream and is NOT included in reaction
    formulation or reaction total flow.
    """

    # --------------------------------------------------------
    # Read BO parameters
    # --------------------------------------------------------

    current_density_mA_cm2 = float(
        parameters["current_density"]
    )

    applied_charge_F_mol = float(
        parameters["applied_charge"]
    )

    mb_target_mg_L = float(
        parameters["substrate_concentration"]
    )

    electrolyte_target_M = float(
        parameters["electrolyte_concentration"]
    )

    cell_name = str(
        parameters["cell"]
    )

    electrolyte_name = str(
        parameters["electrolyte"]
    )


    # --------------------------------------------------------
    # Validate categorical variables
    # --------------------------------------------------------

    if cell_name not in CELL_METADATA:
        raise ValueError(
            f"Unknown cell '{cell_name}'. "
            f"Available cells: {list(CELL_METADATA)}"
        )

    if electrolyte_name not in ELECTROLYTE_METADATA:
        raise ValueError(
            f"Unknown electrolyte '{electrolyte_name}'. "
            f"Available electrolytes: "
            f"{list(ELECTROLYTE_METADATA)}"
        )


    # --------------------------------------------------------
    # Validate continuous variables
    # --------------------------------------------------------

    if current_density_mA_cm2 <= 0:
        raise ValueError(
            "current_density must be > 0 mA/cm2."
        )

    if applied_charge_F_mol <= 0:
        raise ValueError(
            "applied_charge must be > 0 F/mol."
        )

    if mb_target_mg_L <= 0:
        raise ValueError(
            "substrate_concentration must be > 0 mg/L."
        )

    if mb_target_mg_L >= MB_STOCK_CONCENTRATION_MG_L:
        raise ValueError(
            "Target MB concentration must be lower than "
            "the MB stock concentration."
        )

    if electrolyte_target_M <= 0:
        raise ValueError(
            "electrolyte_concentration must be > 0 mol/L."
        )


    # --------------------------------------------------------
    # Selected cell metadata
    # --------------------------------------------------------

    cell = CELL_METADATA[cell_name]

    electrode_area_cm2 = float(
        cell["area_cm2"]
    )

    cell_volume_uL = float(
        cell["volume_uL"]
    )

    cell_volume_ml = (
        cell_volume_uL / 1000.0
    )

    bkk_channel = int(
        cell["bkk_channel"]
    )

    cell_port = int(
        cell["cell_port"]
    )


    # --------------------------------------------------------
    # Selected electrolyte metadata
    # --------------------------------------------------------

    electrolyte = ELECTROLYTE_METADATA[
        electrolyte_name
    ]

    electrolyte_stock_M = float(
        electrolyte["stock_concentration_M"]
    )

    electrolyte_spm_port = int(
        electrolyte["spm_port"]
    )

    if electrolyte_target_M >= electrolyte_stock_M:
        raise ValueError(
            "Target electrolyte concentration must be lower "
            "than the electrolyte stock concentration."
        )


    # ========================================================
    # 1. CURRENT DENSITY -> CURRENT
    # ========================================================

    total_current_mA = (
        current_density_mA_cm2
        * electrode_area_cm2
    )

    total_current_A = (
        total_current_mA / 1000.0
    )


    # ========================================================
    # 2. MB mg/L -> mM
    # ========================================================

    # Numerically:
    #
    # mM = mg/L / molecular weight [g/mol]

    mb_target_mM = (
        mb_target_mg_L
        / MB_MW_G_MOL
    )


    # ========================================================
    # 3. APPLIED CHARGE -> TOTAL FLOW
    # ========================================================

    # Q[mL/min] =
    #
    # I[A]
    # ------------------------------------ * 60 * 1e6
    # F[C/mol] * q[F/mol] * C_MB[mM]

    total_flow_ml_min = (
        (
            total_current_A
            / FARADAY_CONSTANT
            / applied_charge_F_mol
        )
        * 60.0
        * 1e6
        / mb_target_mM
    )

    if total_flow_ml_min <= 0:
        raise ValueError(
            "Computed total flow is <= 0 mL/min."
        )

    if total_flow_ml_min < MIN_TOTAL_FLOW_ML_MIN:
        raise ValueError(
            f"Computed total flow "
            f"({total_flow_ml_min:.3f} mL/min) "
            f"is below minimum allowed flow "
            f"({MIN_TOTAL_FLOW_ML_MIN:.3f} mL/min)."
        )


    # ========================================================
    # 4. PUMP 1 — MB STOCK
    # ========================================================

    flow_mb_ml_min = (
        total_flow_ml_min
        * mb_target_mg_L
        / MB_STOCK_CONCENTRATION_MG_L
    )


    # ========================================================
    # 5. SPM+ — ELECTROLYTE STOCK
    # ========================================================

    flow_electrolyte_ml_min = (
        total_flow_ml_min
        * electrolyte_target_M
        / electrolyte_stock_M
    )

    flow_electrolyte_uL_min = (
        flow_electrolyte_ml_min
        * 1000.0
    )


    # ========================================================
    # 6. PUMP 2 — WATER
    # ========================================================

    flow_water_ml_min = (
        total_flow_ml_min
        - flow_mb_ml_min
        - flow_electrolyte_ml_min
    )


    # ========================================================
    # 7. FEASIBILITY CHECKS
    # ========================================================

    if flow_mb_ml_min <= 0:
        raise ValueError(
            "Computed MB flow must be > 0."
        )

    if flow_electrolyte_ml_min <= 0:
        raise ValueError(
            "Computed electrolyte flow must be > 0."
        )

    if flow_water_ml_min < 0:
        raise ValueError(
            "Computed water flow is negative. "
            "The requested concentrations cannot be produced "
            "from the selected stock solutions."
        )

    if flow_electrolyte_uL_min < SPM_MIN_FLOW_UL_MIN:
        raise ValueError(
            f"SPM+ electrolyte flow "
            f"({flow_electrolyte_uL_min:.1f} uL/min) "
            f"is below practical minimum "
            f"({SPM_MIN_FLOW_UL_MIN:.1f} uL/min)."
        )


    # ========================================================
    # 8. MASS BALANCE
    # ========================================================

    calculated_total_flow = (
        flow_mb_ml_min
        + flow_water_ml_min
        + flow_electrolyte_ml_min
    )

    if abs(
        calculated_total_flow
        - total_flow_ml_min
    ) > 1e-9:
        raise RuntimeError(
            "Internal flow mass-balance error."
        )


    # ========================================================
    # 9. CELL RESIDENCE TIME
    # ========================================================

    residence_time_cell_s = (
        cell_volume_ml
        / total_flow_ml_min
        * 60.0
    )


    # ========================================================
    # 10. PRE-ELECTROCHEMISTRY FLUSH TIME
    # ========================================================

    # Flush:
    #
    # upstream tubing/mixer volume
    # +
    # N cell volumes

    pre_echem_flush_volume_ml = (
        UPSTREAM_DEAD_VOLUME_ML
        + PRE_ECHEM_CELL_VOLUMES
        * cell_volume_ml
    )

    pre_echem_flush_time_s = (
        pre_echem_flush_volume_ml
        / total_flow_ml_min
        * 60.0
    )


    # ========================================================
    # 11. POST-CURRENT / UV-VIS WAIT
    # ========================================================

    downstream_transport_time_s = (
        DOWNSTREAM_DEAD_VOLUME_ML
        / total_flow_ml_min
        * 60.0
    )

    electrochemical_stabilization_time_s = (
        STEADY_STATE_CELL_RESIDENCE_TIMES
        * residence_time_cell_s
    )

    measurement_ready_time_s = (
        electrochemical_stabilization_time_s
        + downstream_transport_time_s
    )


    # ========================================================
    # 12. ESTIMATE SPM+ CAPACITY
    # ========================================================

    spm_available_time_min = (
        SPM_LOAD_VOLUME_UL
        / flow_electrolyte_uL_min
    )


    # ========================================================
    # RETURN OPERATING POINT
    # ========================================================

    return {
        # Requested BO variables
        "cell": cell_name,
        "electrolyte": electrolyte_name,

        "current_density_mA_cm2":
            current_density_mA_cm2,

        "applied_charge_F_mol":
            applied_charge_F_mol,

        "mb_target_mg_L":
            mb_target_mg_L,

        "electrolyte_target_M":
            electrolyte_target_M,

        # Cell metadata
        "structure":
            cell["structure"],

        "electrode_area_cm2":
            electrode_area_cm2,

        "cell_volume_uL":
            cell_volume_uL,

        "gap_mm":
            cell["gap_mm"],

        "bkk_channel":
            bkk_channel,

        "cell_port":
            cell_port,

        # Electrolyte metadata
        "electrolyte_stock_M":
            electrolyte_stock_M,

        "electrolyte_spm_port":
            electrolyte_spm_port,

        "spm_delivery_port":
            SPM_DELIVERY_PORT,

        # Electrical
        "total_current_mA":
            total_current_mA,

        "total_current_A":
            total_current_A,

        # Concentration
        "mb_target_mM":
            mb_target_mM,

        # Flows
        "total_flow_ml_min":
            total_flow_ml_min,

        "flow_mb_ml_min":
            flow_mb_ml_min,

        "flow_water_ml_min":
            flow_water_ml_min,

        "flow_electrolyte_ml_min":
            flow_electrolyte_ml_min,

        "flow_electrolyte_uL_min":
            flow_electrolyte_uL_min,

        # Hydraulics
        "residence_time_cell_s":
            residence_time_cell_s,

        "pre_echem_flush_volume_ml":
            pre_echem_flush_volume_ml,

        "pre_echem_flush_time_s":
            pre_echem_flush_time_s,

        "downstream_transport_time_s":
            downstream_transport_time_s,

        "electrochemical_stabilization_time_s":
            electrochemical_stabilization_time_s,

        "measurement_ready_time_s":
            measurement_ready_time_s,

        # SPM+
        "spm_available_time_min":
            spm_available_time_min,
    }


# ============================================================
# CELL SELECTION
# ============================================================

def _verify_cell_route(runner, cell_port):
    """Check the OPC inlet command and the AMF outlet's reported position."""
    _verify_opc_values(runner, ((CELL_INLET_VALVE_TAG, cell_port),),
                       f"cell inlet port {cell_port}")
    valve = getattr(runner, "cell_valve", None)
    if valve is None:
        raise RuntimeError("AMF cell outlet valve is not configured.")
    detail = "no position reported"
    for attempt in range(OPC_CHECK_ATTEMPTS):
        try:
            position = valve.get_position()
            if not isinstance(position, bool) and float(position) == cell_port:
                return
            detail = f"reported {position!r}"
        except Exception as exc:
            detail = f"position unavailable ({exc})"
        if attempt + 1 < OPC_CHECK_ATTEMPTS:
            time.sleep(OPC_CHECK_INTERVAL_S)
    raise RuntimeError(f"Could not verify AMF cell outlet port {cell_port}: {detail}.")


def _select_cell(runner, cell_port):
    """Select matching inlet/outlet paths with power and all feeds stopped."""
    if isinstance(cell_port, bool) or not isinstance(cell_port, int) or cell_port not in (1, 2, 3):
        raise ValueError("Cell routing port must be 1, 2 or 3.")

    if not hasattr(runner, "cell_valve") or runner.cell_valve is None:
        raise RuntimeError(
            "AMF cell outlet valve is not configured."
        )

    _verify_opc_values(runner, POWER_OFF_TAGS + FEED_STOP_TAGS + ((PUMP_CLEANING_TAG, 0),),
                       "power and pumps stopped before cell routing")
    runner.spm.get_syringe_state()
    runner._mb_selected_cell_port = None
    runner.move_cell_valve(cell_port)
    _required_write(runner, CELL_INLET_VALVE_TAG, cell_port)
    _verify_cell_route(runner, cell_port)
    runner._mb_selected_cell_port = cell_port
    print(f"Cell routing verified: ROT_VALVE inlet port {cell_port}; AMF outlet port {cell_port}.")


# ============================================================
# BKK CHANNEL SELECTION
# ============================================================

def _select_bkk_channel(
    runner,
    selected_channel
):
    """
    Select exactly one BKK output channel.

    Selected channel = 1
    Other channels    = 0
    """

    if selected_channel not in BKK_CHANNEL_TAGS:
        raise ValueError(
            f"Invalid BKK channel: {selected_channel}"
        )

    for channel, tag in BKK_CHANNEL_TAGS.items():

        value = (
            1
            if channel == selected_channel
            else 0
        )

        runner.opc.write_value(
            tag,
            value
        )

    print(
        f"BKK channel {selected_channel} selected."
    )


# ============================================================
# SPM+ PREPARATION
# ============================================================

def _invalidate_spm_contents(runner):
    # Contents are deliberately kept only on the live runner. A new/resumed
    # runner must not infer the physical syringe contents from saved results.
    runner._mb_spm_contents = None


def _spm_refill_settings(runner, spm):
    config = getattr(runner, "adapter_config", {}) or {}
    waste = config.get("spm_waste_port", SPM_WASTE_PORT)
    cycles = config.get("spm_rinse_cycles", SPM_RINSE_CYCLES)
    rinse = float(config.get("spm_rinse_volume_uL", SPM_RINSE_VOLUME_UL))
    target = float(SPM_LOAD_VOLUME_UL)
    capacity = float(spm.syringe_volume)
    if isinstance(waste, bool) or not isinstance(waste, int) or waste < 1:
        raise ValueError("SPM waste port must be a positive integer.")
    if waste != 1:
        raise ValueError("SPM startup homing always expels through port 1; connect waste to port 1 and set spm_waste_port to 1.")
    source_ports = {data["spm_port"] for data in ELECTROLYTE_METADATA.values()}
    if waste == SPM_DELIVERY_PORT or waste in source_ports:
        raise ValueError("SPM waste must use a separate port from electrolyte sources and reactor delivery.")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1:
        raise ValueError("Configure at least one SPM rinse cycle before changing electrolyte.")
    if not math.isfinite(capacity) or not math.isfinite(target) or not 0 < target <= capacity:
        raise ValueError("SPM fill target must fit within the configured syringe capacity.")
    if not math.isfinite(rinse) or not 0 < rinse <= capacity:
        raise ValueError("SPM rinse volume must be positive and fit within the syringe.")
    return waste, rinse, cycles, target


def _prepare_spm_electrolyte(runner, op):
    """Home once at startup; retain matching stock or empty/rinse on a change."""
    spm = getattr(runner, "spm", None)
    if spm is None:
        raise RuntimeError("runner.spm is not configured.")

    try:
        waste, rinse, cycles, target = _spm_refill_settings(runner, spm)
        _initialize_spm_campaign(runner, waste)
        state = spm.get_syringe_state()
        # A different source or stock concentration also requires a changeover.
        identity = (op["electrolyte"], float(op["electrolyte_stock_M"]), op["electrolyte_spm_port"])
        previous = getattr(runner, "_mb_spm_contents", None)
        remaining = state["volume_uL"]
        _invalidate_spm_contents(runner)

        if previous == identity:
            print(
                f"SPM+: retaining {identity[0]} ({identity[1]:g} M stock); "
                f"{remaining:.2f} µL remains, topping up to {target:.0f} µL."
            )
            if remaining > target + state["tolerance_uL"]:
                raise RuntimeError("SPM contains more than the fill target; reset it via waste before reuse.")
        else:
            reason = "contents unknown" if previous is None else f"changing stock from {previous[0]} to {identity[0]}"
            print(f"SPM+: {reason}; emptying through waste port {waste}.")
            runner.move_spm_valve(waste)
            runner.set_spm_flow(SPM_DISCARD_FLOW_UL_MIN)
            runner.move_spm_to_volume(0.0)
            for cycle in range(1, cycles + 1):
                print(f"SPM+: rinse {cycle}/{cycles}, {rinse:.0f} µL of incoming {identity[0]} to waste.")
                runner.move_spm_valve(identity[2])
                runner.set_spm_flow(SPM_LOADING_FLOW_UL_MIN)
                runner.move_spm_to_volume(rinse)
                runner.move_spm_valve(waste)
                runner.set_spm_flow(SPM_DISCARD_FLOW_UL_MIN)
                runner.move_spm_to_volume(0.0)

        runner.move_spm_valve(identity[2])
        runner.set_spm_flow(SPM_LOADING_FLOW_UL_MIN)
        # Absolute target: a partly full syringe receives only the missing volume.
        runner.move_spm_to_volume(target)
        runner.move_spm_valve(SPM_DELIVERY_PORT)
        runner.set_spm_flow(op["flow_electrolyte_uL_min"])
        runner._mb_spm_contents = identity
        runner._mb_spm_delivery_active = False
        print(f"SPM+: ready with {target:.0f} µL of {identity[0]}; delivery flow {op['flow_electrolyte_uL_min']:.1f} µL/min.")
    except Exception:
        _invalidate_spm_contents(runner)
        raise


def _initialize_spm_campaign(runner, waste):
    if not getattr(runner, "_mb_spm_initialized", False):
        _invalidate_spm_contents(runner)
        print("SPM+: first preparation of this campaign; homing the syringe to waste.")
        runner.home_spm_to_waste(waste_port=waste)
        runner._mb_spm_delivery_active = False
        runner._mb_spm_initialized = True
    elif getattr(runner, "_mb_spm_delivery_active", False):
        _stop_spm_delivery(runner)
    runner.spm.get_syringe_state()  # Refuse valve-mode changes with an uncertain pump.


# ============================================================
# START SPM+ DELIVERY
# ============================================================

def _start_spm_delivery(runner):
    """Use the SDK's nonblocking movement; keep all serial access on one thread."""
    if getattr(runner, "_mb_spm_delivery_active", False):
        raise RuntimeError("SPM delivery is already active.")
    if getattr(runner, "_mb_spm_contents", None) is None:
        raise RuntimeError("SPM contents are unknown; prepare the electrolyte before delivery.")
    try:
        state = runner.spm.get_syringe_state()
        if state["volume_uL"] <= state["tolerance_uL"]:
            raise RuntimeError("SPM syringe is empty; delivery cannot start.")
        runner._mb_spm_delivery_active = True
        runner.move_spm_to_volume(0.0, block=False)
        print("SPM+ electrolyte delivery started (nonblocking command).")
    except Exception:
        _invalidate_spm_contents(runner)
        raise


# ============================================================
# STOP SPM+
# ============================================================

def _stop_spm_delivery(runner, best_effort=False):
    """
    Stop SPM+ immediately, leaving the remaining liquid
    inside the syringe.
    """

    if getattr(runner, "spm", None) is None:
        return

    try:
        state = runner.stop_spm()
        runner._mb_spm_delivery_active = False
        print(f"SPM+ electrolyte delivery stopped; {state['volume_uL']:.2f} µL retained.")
        return state

    except Exception as exc:
        _invalidate_spm_contents(runner)
        print(f"Warning: could not verify SPM+ stop: {exc}")
        if not best_effort:
            raise RuntimeError("SPM stop/status could not be verified; refill is blocked.") from exc


# ============================================================
# SAFE SHUTDOWN
# ============================================================

def emergency_shutdown(runner):
    """
    Best-effort safe shutdown.

    Called if hardware preparation fails and can also be
    called by the runner after other experiment errors.
    """

    _invalidate_spm_contents(runner)
    runner._mb_feed_shutdown_confirmed = False
    print("")
    print("!!! SAFE SHUTDOWN STARTED !!!")

    # Power supply OFF first
    try:
        runner.turn_off_power_supply()
        print("Power supply OFF.")
    except Exception as exc:
        print(
            f"Warning: power supply shutdown failed: {exc}"
        )

    # Pump 1 OFF
    try:
        runner.opc.write_value(
            PUMP_MB_TAG,
            0
        )
        print("Pump 1 stopped.")
    except Exception as exc:
        print(
            f"Warning: Pump 1 stop failed: {exc}"
        )

    # Pump 2 OFF
    try:
        runner.opc.write_value(
            PUMP_WATER_TAG,
            0
        )
        print("Pump 2 stopped.")
    except Exception as exc:
        print(
            f"Warning: Pump 2 stop failed: {exc}"
        )

    # Pump 3 OFF
    try:
        runner.opc.write_value(
            PUMP_ANALYTICAL_DILUTION_TAG,
            0
        )
        print("Pump 3 stopped.")
    except Exception as exc:
        print(
            f"Warning: Pump 3 stop failed: {exc}"
        )

    # SPM+ stop
    _stop_spm_delivery(
        runner, best_effort=True
    )

    try:
        _stop_cleaning_pump(runner)
    except Exception as exc:
        print(f"Warning: cleaning-pump stop could not be verified: {exc}")

    print("!!! SAFE SHUTDOWN ATTEMPT COMPLETE — CHECK ANY WARNINGS ABOVE !!!")
    print("")


# ============================================================
# LOGGING
# ============================================================

def _log_operating_point(
    op,
    status="prepared",
    error_message=""
):
    """
    Append one operating-point record to CSV.

    Later the measurement and objective values can be added
    through a second result logger or by extending this record.
    """

    record = {
        "timestamp":
            datetime.now().isoformat(),

        "status":
            status,

        "error_message":
            error_message,

        "cell":
            op.get("cell"),

        "structure":
            op.get("structure"),

        "cell_port":
            op.get("cell_port"),

        "bkk_channel":
            op.get("bkk_channel"),

        "electrolyte":
            op.get("electrolyte"),

        "electrolyte_spm_port":
            op.get("electrolyte_spm_port"),

        "current_density_mA_cm2":
            op.get("current_density_mA_cm2"),

        "total_current_mA":
            op.get("total_current_mA"),

        "total_current_A":
            op.get("total_current_A"),

        "applied_charge_F_mol":
            op.get("applied_charge_F_mol"),

        "mb_target_mg_L":
            op.get("mb_target_mg_L"),

        "electrolyte_target_M":
            op.get("electrolyte_target_M"),

        "total_flow_ml_min":
            op.get("total_flow_ml_min"),

        "flow_mb_ml_min":
            op.get("flow_mb_ml_min"),

        "flow_water_ml_min":
            op.get("flow_water_ml_min"),

        "flow_electrolyte_ml_min":
            op.get("flow_electrolyte_ml_min"),

        "flow_electrolyte_uL_min":
            op.get("flow_electrolyte_uL_min"),

        "residence_time_cell_s":
            op.get("residence_time_cell_s"),

        "pre_echem_flush_time_s":
            op.get("pre_echem_flush_time_s"),

        "measurement_ready_time_s":
            op.get("measurement_ready_time_s"),

        "spm_available_time_min":
            op.get("spm_available_time_min"),
    }

    file_exists = LOG_FILE.exists()

    with LOG_FILE.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=record.keys()
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            record
        )


# ============================================================
# EXPERIMENT SUMMARY
# ============================================================

def _print_operating_point(op):

    print("")
    print("=" * 65)
    print("METHYLENE BLUE ELECTROOXIDATION")
    print("=" * 65)

    print(
        f"Cell                  = {op['cell']}"
    )

    print(
        f"Structure             = {op['structure']}"
    )

    print(
        f"Cell inlet/outlet     = {op['cell_port']} / {op['cell_port']}"
    )

    print(
        f"Electrode area        = "
        f"{op['electrode_area_cm2']:.3f} cm2"
    )

    print(
        f"Cell volume           = "
        f"{op['cell_volume_uL']:.2f} uL"
    )

    print(
        f"Electrolyte           = "
        f"{op['electrolyte']}"
    )

    print(
        f"SPM source port       = "
        f"{op['electrolyte_spm_port']}"
    )

    print(
        f"BKK channel           = "
        f"{op['bkk_channel']}"
    )

    print("")
    print("--- ELECTROCHEMISTRY ---")

    print(
        f"Current density       = "
        f"{op['current_density_mA_cm2']:.3f} mA/cm2"
    )

    print(
        f"Total current         = "
        f"{op['total_current_mA']:.3f} mA"
    )

    print(
        f"Applied charge        = "
        f"{op['applied_charge_F_mol']:.3f} F/mol"
    )

    print("")
    print("--- CONCENTRATIONS ---")

    print(
        f"MB target             = "
        f"{op['mb_target_mg_L']:.2f} mg/L"
    )

    print(
        f"Electrolyte target    = "
        f"{op['electrolyte_target_M']:.4f} M"
    )

    print("")
    print("--- FLOWS ---")

    print(
        f"Total flow            = "
        f"{op['total_flow_ml_min']:.4f} mL/min"
    )

    print(
        f"Pump 1 MB             = "
        f"{op['flow_mb_ml_min']:.4f} mL/min"
    )

    print(
        f"Pump 2 water          = "
        f"{op['flow_water_ml_min']:.4f} mL/min"
    )

    print(
        f"SPM+ electrolyte      = "
        f"{op['flow_electrolyte_ml_min']:.4f} mL/min "
        f"({op['flow_electrolyte_uL_min']:.1f} uL/min)"
    )

    print("")
    print("--- TIMING ---")

    print(
        f"Cell residence time   = "
        f"{op['residence_time_cell_s']:.2f} s"
    )

    print(
        f"Pre-echem flush time  = "
        f"{op['pre_echem_flush_time_s']:.2f} s"
    )

    print(
        f"Measurement wait      = "
        f"{op['measurement_ready_time_s']:.2f} s"
    )

    print(
        f"SPM capacity          = "
        f"{op['spm_available_time_min']:.2f} min"
    )

    print("=" * 65)


# ============================================================
# HARDWARE PREPARATION
# ============================================================

def prepare_hardware(
    runner,
    parameters
):
    """
    Configure the MB electrochemical experiment.

    Sequence
    --------
    1. Compute operating point.
    2. Print and log conditions.
    3. Exit before hardware movement in full simulation.
    4. Ensure PSU and feed pumps OFF; initialize the SPM once via waste.
    5. Establish/read back electrolysis mode; select matching inlet/outlet cell ports.
    6. Load selected electrolyte into SPM+.
    7. Set Pump 1 MB flow.
    8. Set Pump 2 water flow.
    9. Start SPM+ electrolyte flow.
    10. Flush upstream path + selected cell.
    11. Select BKK channel.
    12. Set current.
    13. Turn PSU ON.
    14. Wait until new electrochemical condition should
        have reached UV/Vis.
    """

    op = _compute_operating_point(
        parameters
    )

    _print_operating_point(
        op
    )

    # --------------------------------------------------------
    # Full simulation
    # --------------------------------------------------------

    if runner.simulation_mode not in [
        "off",
        "hybrid",
    ]:

        print(
            "Full simulation mode: "
            "hardware commands skipped."
        )

        _log_operating_point(
            op,
            status="simulation"
        )

        return op


    # --------------------------------------------------------
    # Real / hybrid hardware preparation
    # --------------------------------------------------------

    try:
        runner._mb_feed_shutdown_confirmed = False

        # ====================================================
        # 1. POWER SUPPLY OFF
        # ====================================================

        print(
            "Ensuring power supply is OFF..."
        )

        _ensure_power_off(runner)
        _stop_cleaning_pump(runner)
        _stop_reagent_feeds(runner)
        waste, _, _, _ = _spm_refill_settings(runner, runner.spm)
        _initialize_spm_campaign(runner, waste)
        _set_flow_mode(runner, "electrolysis")


        # ====================================================
        # 2. SELECT CELL
        # ====================================================

        print(
            f"Selecting {op['cell']} "
            f"on inlet and outlet port "
            f"{op['cell_port']}..."
        )

        _select_cell(
            runner,
            op["cell_port"]
        )


        # ====================================================
        # 3. PREPARE SPM+
        # ====================================================

        _prepare_spm_electrolyte(
            runner,
            op
        )


        # ====================================================
        # 4. PUMP 1 — MB
        # ====================================================

        pump1_uL_min = round(
            op["flow_mb_ml_min"]
            * 1000.0,
            0
        )

        print(
            f"Setting Pump 1 MB = "
            f"{pump1_uL_min:.0f} uL/min"
        )

        runner.opc.write_value(
            PUMP_MB_TAG,
            pump1_uL_min
        )


        # ====================================================
        # 5. PUMP 2 — WATER
        # ====================================================

        pump2_uL_min = round(
            op["flow_water_ml_min"]
            * 1000.0,
            0
        )

        print(
            f"Setting Pump 2 water = "
            f"{pump2_uL_min:.0f} uL/min"
        )

        runner.opc.write_value(
            PUMP_WATER_TAG,
            pump2_uL_min
        )


        # ====================================================
        # 6. START ELECTROLYTE
        # ====================================================

        print(
            "Starting SPM+ electrolyte delivery..."
        )

        _start_spm_delivery(
            runner
        )


        # ====================================================
        # 7. PRE-ELECTROCHEMISTRY FLUSH
        # ====================================================

        print(
            f"Flushing upstream path and "
            f"{PRE_ECHEM_CELL_VOLUMES:.1f} cell volumes "
            f"for "
            f"{op['pre_echem_flush_time_s']:.1f} s..."
        )

        time.sleep(
            op["pre_echem_flush_time_s"]
        )


        # ====================================================
        # 8. SELECT BKK CHANNEL
        # ====================================================

        _verify_cell_route(runner, op["cell_port"])

        _select_bkk_channel(
            runner,
            op["bkk_channel"]
        )


        # ====================================================
        # 9. SET CURRENT
        # ====================================================

        print(
            f"Setting current = "
            f"{op['total_current_A']:.6f} A"
        )

        runner.set_current(
            op["total_current_A"]
        )


        # ====================================================
        # 10. POWER ON
        # ====================================================

        print(
            "Turning power supply ON..."
        )

        runner.turn_on_power_supply()


        # ====================================================
        # 11. WAIT FOR ELECTROCHEMICAL RESPONSE
        # ====================================================

        print(
            f"Waiting "
            f"{op['measurement_ready_time_s']:.1f} s "
            f"before UV/Vis measurement..."
        )

        time.sleep(
            op["measurement_ready_time_s"]
        )


        # ====================================================
        # 12. LOG SUCCESSFUL PREPARATION
        # ====================================================

        _log_operating_point(
            op,
            status="ready_for_measurement"
        )

        return op


    except Exception as exc:

        _invalidate_spm_contents(runner)
        print(
            f"ERROR during hardware preparation: {exc}"
        )

        try:
            _log_operating_point(op, status="hardware_error", error_message=str(exc))
        except Exception as log_error:
            print(f"Could not log preparation error: {log_error}")

        emergency_shutdown(
            runner
        )

        raise


# ============================================================
# NORMAL SHUTDOWN / END OF EXPERIMENT
# ============================================================

def stop_pumps(runner, parameters):
    """Attempt every stop; automatic cleaning requires verified shutdown."""
    if runner.simulation_mode not in ("off", "hybrid"):
        return
    runner._mb_feed_shutdown_confirmed = False
    errors = []
    for action in (
        lambda: _ensure_power_off(runner),
        lambda: _stop_spm_delivery(runner),
        lambda: _stop_reagent_feeds(runner),
        lambda: _stop_cleaning_pump(runner),
    ):
        try:
            action()
        except Exception as exc:
            errors.append(exc)
            print(f"Warning: shutdown could not be verified: {exc}")
    if errors:
        raise RuntimeError("Feed shutdown is uncertain; automatic cleaning is blocked: "
                           + "; ".join(map(str, errors))) from errors[0]
    runner._mb_feed_shutdown_confirmed = True
    print("Power supply and pumps stopped; remaining SPM electrolyte retained.")


# ============================================================
# AUTOSAMPLER / DOWNSTREAM FLOW INFORMATION
# ============================================================

def autosampler_flow_rate(
    runner,
    parameters
):
    """
    Reaction flow reaching downstream analytical system.

    Pump 3 analytical dilution is intentionally NOT included.
    """

    op = _compute_operating_point(
        parameters
    )

    return float(
        op["total_flow_ml_min"]
    )


# ============================================================
# CLEANUP
# ============================================================

def _cleaning_settings(runner):
    config = getattr(runner, "adapter_config", {}) or {}
    flow = float(config.get("cleaning_flow_ml_min", CLEANING_FLOW_ML_MIN))
    duration = float(config.get("cleaning_duration_s", CLEANING_DURATION_S))
    if not math.isfinite(flow) or flow <= 0:
        raise ValueError("Cleaning flow must be finite and positive, in mL/min.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Cleaning duration must be finite and positive, in seconds.")
    # Legacy cleaning_water_port settings have no effect: water is supplied
    # directly, and changing ROT_VALVE would now select a different cell.
    return flow, duration


def cleanup(runner, parameters):
    """Rinse the selected cell with water; retain the separate SPM contents.

    Called after feed shutdown. Failed experiments or unverified shutdowns
    never start Pump 6. The same electrolysis commands are reasserted and
    read back again at the start of every experiment. Both cell selectors
    stay on the selected cell; their readbacks are checked before water flow
    and after Pump 6 stops. Pump 6 receives water directly.
    """
    if runner.simulation_mode not in ("off", "hybrid"):
        print("Full simulation mode: skipping water cleaning.")
        return
    if not getattr(runner, "_experiment_completed", True):
        print("Experiment failed: skipping automatic water cleaning; shutdown only.")
        return
    if not getattr(runner, "_mb_feed_shutdown_confirmed", False):
        print("Feed shutdown is unverified: skipping automatic water cleaning.")
        return

    flow, duration = _cleaning_settings(runner)
    cell_name = str((parameters or {}).get("cell", ""))
    if cell_name not in CELL_METADATA:
        raise ValueError("Water cleaning requires the selected cell (EC1, EC2 or EC3).")
    cell_port = int(CELL_METADATA[cell_name]["cell_port"])
    error = None
    cleaning_stopped = False
    try:
        _ensure_power_off(runner)
        _verify_opc_values(runner, FEED_STOP_TAGS, "reagent feeds stopped before cleaning")
        runner.spm.get_syringe_state()
        _stop_cleaning_pump(runner)
        _set_flow_mode(runner, "cleaning")
        _verify_cell_route(runner, cell_port)
        print(f"Water cleaning: Pump 6 at {flow:g} mL/min for {duration:g} s "
              f"({flow * duration / 60.0:g} mL), inlet/outlet cell port {cell_port} retained.")
        # Set the flag before attempting start: a lost reply leaves motion uncertain.
        runner._mb_cleaning_active = True
        _required_write(runner, PUMP_CLEANING_TAG, flow)
        _verify_opc_values(runner, ((PUMP_CLEANING_TAG, flow),), "cleaning flow command")
        time.sleep(duration)
    except BaseException as exc:
        error = exc
    finally:
        try:
            _stop_cleaning_pump(runner)
            cleaning_stopped = True
        except Exception as exc:
            if error is None:
                error = exc
            else:
                print(f"Cleaning-pump shutdown also failed: {exc}")
        # Never switch the water path while Pump 6 might still be flowing.
        if cleaning_stopped:
            try:
                _set_flow_mode(runner, "electrolysis")
                _verify_cell_route(runner, cell_port)
            except Exception as exc:
                if error is None:
                    error = exc
                else:
                    print(f"Restoring electrolysis mode or confirming the cell route also failed: {exc}")
    if error is not None:
        runner._mb_feed_shutdown_confirmed = False
        raise error
    print("Water cleaning complete; Pump 6 stopped and electrolysis mode restored.")


def cleaning_electrochemical_cell(runner):
    """Route reproducibility/maintenance cleaning through the MB water-only path."""
    if runner.simulation_mode not in ("off", "hybrid"):
        return
    if getattr(runner, "closed", False):
        raise RuntimeError("The MB runner is disconnected; reconnect before cleaning.")
    if not getattr(runner, "_experiment_completed", False):
        raise RuntimeError("Additional MB cleaning requires a successfully completed experiment.")
    port = getattr(runner, "_mb_selected_cell_port", None)
    cell = next((name for name, info in CELL_METADATA.items() if info["cell_port"] == port), None)
    if cell is None:
        raise RuntimeError("No verified MB cell route is available for additional cleaning.")
    parameters = {"cell": cell}
    stop_pumps(runner, parameters)
    cleanup(runner, parameters)
