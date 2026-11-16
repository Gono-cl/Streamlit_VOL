from __future__ import annotations
import time


# ============================================================
# CONSTANTS
# ============================================================

FARADAY_CONSTANT = 96485.33212  # C/mol

# Methylene blue chloride, anhydrous.
# Change this if the actual material used is a hydrate or another form.
MB_MW_G_MOL = 319.85

# Fixed concentrated MB stock delivered by Pump 1
MB_STOCK_CONCENTRATION_MG_L = 1000.0

# Minimum desired total reaction flow
MIN_TOTAL_FLOW_ML_MIN = 0.5

# Used only to estimate how long to flush/fill the selected cell
FILLING_MARGIN_FACTOR = 1.5


# ============================================================
# CELL METADATA
# ============================================================

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

# For now, all electrolyte stocks are assumed to be 1.0 M.
# This can easily be changed later.
#
# SPM+:
#   port 1 -> Na2SO4
#   port 2 -> NaCl
#   port 3 -> NaNO3
#   port 4 -> delivery

ELECTROLYTE_METADATA = {
    "Na2SO4": {
        "spm_port": 1,
        "stock_concentration_M": 0.4,
    },
    "NaCl": {
        "spm_port": 2,
        "stock_concentration_M": 0.4,
    },
    "NaNO3": {
        "spm_port": 3,
        "stock_concentration_M": 0.4,
    },
}

SPM_DELIVERY_PORT = 4


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

    Expected BO parameters
    ----------------------
    current_density
        mA/cm2

    applied_charge
        F/mol

    substrate_concentration
        Final methylene blue concentration at reactor inlet, mg/L

    electrolyte_concentration
        Final electrolyte concentration at reactor inlet, mol/L

    cell
        EC1, EC2 or EC3

    electrolyte
        Na2SO4, NaCl or NaNO3


    Feed architecture
    -----------------
    Pump 1 = 1000 mg/L MB stock
    Pump 2 = dilution water
    SPM+   = concentrated electrolyte stock

    Pump 3 is NOT included here because it is used only for
    analytical dilution before UV/Vis.
    """

    # --------------------------------------------------------
    # Read BO parameters
    # --------------------------------------------------------

    current_density_mA_cm2 = float(parameters["current_density"])
    applied_charge_F_mol = float(parameters["applied_charge"])

    mb_target_mg_L = float(parameters["substrate_concentration"])
    electrolyte_target_M = float(parameters["electrolyte_concentration"])

    cell_name = str(parameters["cell"])
    electrolyte_name = str(parameters["electrolyte"])


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
            f"Available electrolytes: {list(ELECTROLYTE_METADATA)}"
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
    # Load selected cell metadata
    # --------------------------------------------------------

    cell = CELL_METADATA[cell_name]

    electrode_area_cm2 = float(cell["area_cm2"])
    cell_volume_uL = float(cell["volume_uL"])
    bkk_channel = int(cell["bkk_channel"])
    cell_port = int(cell["cell_port"])


    # --------------------------------------------------------
    # Load selected electrolyte metadata
    # --------------------------------------------------------

    electrolyte = ELECTROLYTE_METADATA[electrolyte_name]

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
    # 1. CURRENT DENSITY -> TOTAL CURRENT
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
    # 3. APPLIED CHARGE -> TOTAL REACTION FLOW
    # ========================================================

    # Q[mL/min] =
    #
    # I[A]
    # ----------------------- * 60 * 1e6
    # F[C/mol] * q[F/mol] * C[mM]

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


    # ========================================================
    # 4. MINIMUM TOTAL FLOW CONSTRAINT
    # ========================================================

    if total_flow_ml_min < MIN_TOTAL_FLOW_ML_MIN:
        raise ValueError(
            f"Computed total flow "
            f"({total_flow_ml_min:.3f} mL/min) "
            f"is below the minimum allowed flow "
            f"({MIN_TOTAL_FLOW_ML_MIN:.3f} mL/min)."
        )


    # ========================================================
    # 5. PUMP 1: MB STOCK FLOW
    # ========================================================

    # C_final =
    # C_stock * Q_MB / Q_total

    flow_mb_ml_min = (
        total_flow_ml_min
        * mb_target_mg_L
        / MB_STOCK_CONCENTRATION_MG_L
    )


    # ========================================================
    # 6. SPM+: ELECTROLYTE STOCK FLOW
    # ========================================================

    # C_final =
    # C_stock * Q_electrolyte / Q_total

    flow_electrolyte_ml_min = (
        total_flow_ml_min
        * electrolyte_target_M
        / electrolyte_stock_M
    )


    # ========================================================
    # 7. PUMP 2: WATER FLOW
    # ========================================================

    flow_water_ml_min = (
        total_flow_ml_min
        - flow_mb_ml_min
        - flow_electrolyte_ml_min
    )


    # --------------------------------------------------------
    # Check dilution feasibility
    # --------------------------------------------------------

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
            "The requested MB and electrolyte concentrations "
            "cannot be produced from the selected stock solutions."
        )


    # ========================================================
    # 8. CHECK MASS BALANCE
    # ========================================================

    calculated_total = (
        flow_mb_ml_min
        + flow_water_ml_min
        + flow_electrolyte_ml_min
    )

    if abs(
        calculated_total - total_flow_ml_min
    ) > 1e-9:
        raise RuntimeError(
            "Internal flow mass-balance error."
        )


    # ========================================================
    # 9. CELL FILLING / FLUSH TIME
    # ========================================================

    cell_volume_ml = (
        cell_volume_uL / 1000.0
    )

    residence_time_cell_s = (
        cell_volume_ml
        / total_flow_ml_min
        * 60.0
    )

    filling_time_cell_s = (
        residence_time_cell_s
        * FILLING_MARGIN_FACTOR
    )


    # ========================================================
    # RETURN COMPLETE OPERATING POINT
    # ========================================================

    return {

        # BO variables
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

        "electrode_area_cm2":
            electrode_area_cm2,

        "cell_volume_uL":
            cell_volume_uL,

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


        # Derived electrical variables
        "total_current_mA":
            total_current_mA,

        "total_current_A":
            total_current_A,


        # Concentration conversion
        "mb_target_mM":
            mb_target_mM,


        # Derived flow variables
        "total_flow_ml_min":
            total_flow_ml_min,

        "flow_mb_ml_min":
            flow_mb_ml_min,

        "flow_water_ml_min":
            flow_water_ml_min,

        "flow_electrolyte_ml_min":
            flow_electrolyte_ml_min,


        # Hydraulic information
        "residence_time_cell_s":
            residence_time_cell_s,

        "filling_time_cell_s":
            filling_time_cell_s,
    }


# ============================================================
# HARDWARE CONFIGURATION
# ============================================================

# Hitec Zang pumps
#
# Pump 1 = MB stock
# Pump 2 = dilution water
#
# Previous protocols send these setpoints as uL/min,
# therefore calculated mL/min is multiplied by 1000.

PUMP_MB_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1"
PUMP_WATER_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1"


# SPM+
#
# Ports 1, 2, 3 = electrolyte stocks
# Port 4         = reactor delivery

SPM_DELIVERY_PORT = 4

# Loading speed used only when filling the SPM syringe
SPM_LOADING_FLOW_ML_MIN = 5.0

# For now we can load a full 5 mL syringe.
# IMPORTANT:
# Confirm the volume unit expected by your SPMPump.pickup()
# before running this in REAL mode.
SPM_LOAD_VOLUME = 5.0


# ============================================================
# HARDWARE HELPERS
# ============================================================

def _select_cell(runner, cell_port):
    """
    Move the external AMF MultiportValve to the selected cell.

    Expected:
        runner.cell_valve = MultiportValve(...)
    """

    if not hasattr(runner, "cell_valve"):
        raise RuntimeError(
            "runner.cell_valve is not configured."
        )

    valve = runner.cell_valve

    # Adapt to the method exposed by your MultiportValve wrapper.
    if hasattr(valve, "move_valve"):
        valve.move_valve(cell_port)

    elif hasattr(valve, "move"):
        valve.move(cell_port)

    elif hasattr(valve, "set_position"):
        valve.set_position(cell_port)

    else:
        raise RuntimeError(
            "MultiportValve object does not expose "
            "move_valve(), move(), or set_position()."
        )


def _prepare_spm_electrolyte(runner, op):
    """
    Prepare the SPM+ for electrolyte delivery.

    Sequence:
        1. Select electrolyte stock port
        2. Aspirate electrolyte into syringe
        3. Switch internal valve to port 4
        4. Set calculated electrolyte delivery flow

    Actual dispensing is started afterwards.
    """

    if not hasattr(runner, "spm"):
        raise RuntimeError(
            "runner.spm is not configured."
        )

    spm = runner.spm

    electrolyte_port = op["electrolyte_spm_port"]
    electrolyte_flow = op["flow_electrolyte_ml_min"]

    print(
        f"SPM+: selecting electrolyte port "
        f"{electrolyte_port}"
    )

    spm.move_valve(electrolyte_port)

    print(
        f"SPM+: loading electrolyte at "
        f"{SPM_LOADING_FLOW_ML_MIN:.3f} mL/min"
    )

    spm.set_flow_rate(
        SPM_LOADING_FLOW_ML_MIN
    )

    spm.pickup(
        SPM_LOAD_VOLUME
    )

    print(
        f"SPM+: switching to delivery port "
        f"{SPM_DELIVERY_PORT}"
    )

    spm.move_valve(
        SPM_DELIVERY_PORT
    )

    print(
        f"SPM+: electrolyte delivery flow = "
        f"{electrolyte_flow:.4f} mL/min"
    )

    spm.set_flow_rate(
        electrolyte_flow
    )


def _start_spm_delivery(runner):
    """
    Start electrolyte delivery from the loaded SPM+ syringe.

    For now this dispenses the loaded syringe volume.

    Later we can replace this with refill-cycle logic if an
    experiment requires more electrolyte than one syringe volume.
    """

    runner.spm.dispense(
        SPM_LOAD_VOLUME
    )


# ============================================================
# HARDWARE PREPARATION
# ============================================================

def prepare_hardware(runner, parameters):
    """
    Configure and start the MB electrochemical experiment.

    The function:

        1. Calculates the operating point
        2. Prints all calculated conditions
        3. Stops before hardware movement in simulation mode
        4. Selects the electrochemical cell
        5. Prepares the SPM+ electrolyte
        6. Sets MB pump flow
        7. Sets dilution-water pump flow
        8. Starts electrolyte delivery
        9. Waits for cell filling
       10. Sets the electrochemical current

    BKK channel selection will be added once its interface
    is confirmed.

    Pump 3 is intentionally excluded because it belongs to
    the analytical dilution system downstream of the reactor.
    """

    # ========================================================
    # 1. CALCULATE OPERATING POINT
    # ========================================================

    op = _compute_operating_point(
        parameters
    )


    # ========================================================
    # 2. PRINT EXPERIMENT SUMMARY
    # ========================================================

    print("")
    print("=" * 60)
    print("METHYLENE BLUE ELECTROOXIDATION")
    print("=" * 60)

    print(
        f"Cell                 = "
        f"{op['cell']}"
    )

    print(
        f"Cell port            = "
        f"{op['cell_port']}"
    )

    print(
        f"Electrode area       = "
        f"{op['electrode_area_cm2']:.3f} cm2"
    )

    print(
        f"Cell volume          = "
        f"{op['cell_volume_uL']:.2f} uL"
    )

    print(
        f"Electrolyte          = "
        f"{op['electrolyte']}"
    )

    print(
        f"SPM electrolyte port = "
        f"{op['electrolyte_spm_port']}"
    )

    print(
        f"Current density      = "
        f"{op['current_density_mA_cm2']:.3f} mA/cm2"
    )

    print(
        f"Total current        = "
        f"{op['total_current_mA']:.3f} mA"
    )

    print(
        f"Applied charge       = "
        f"{op['applied_charge_F_mol']:.3f} F/mol"
    )

    print(
        f"MB target            = "
        f"{op['mb_target_mg_L']:.2f} mg/L"
    )

    print(
        f"Electrolyte target   = "
        f"{op['electrolyte_target_M']:.4f} M"
    )

    print("")
    print("--- FLOW CALCULATION ---")

    print(
        f"Total flow           = "
        f"{op['total_flow_ml_min']:.4f} mL/min"
    )

    print(
        f"Pump 1 - MB          = "
        f"{op['flow_mb_ml_min']:.4f} mL/min"
    )

    print(
        f"Pump 2 - water       = "
        f"{op['flow_water_ml_min']:.4f} mL/min"
    )

    print(
        f"SPM+ - electrolyte   = "
        f"{op['flow_electrolyte_ml_min']:.4f} mL/min"
    )

    print("")
    print("--- HYDRAULICS ---")

    print(
        f"Cell residence time  = "
        f"{op['residence_time_cell_s']:.2f} s"
    )

    print(
        f"Cell filling time    = "
        f"{op['filling_time_cell_s']:.2f} s"
    )

    print("=" * 60)


    # ========================================================
    # 3. SIMULATION CHECK
    # ========================================================

    if runner.simulation_mode not in [
        "off",
        "hybrid",
    ]:

        print(
            "Full simulation mode: "
            "hardware commands skipped."
        )

        return op


    # ========================================================
    # 4. POWER SUPPLY MUST BE OFF
    # ========================================================

    print(
        "Ensuring power supply is OFF..."
    )

    runner.turn_off_power_supply()


    # ========================================================
    # 5. SELECT CELL
    # ========================================================

    print(
        f"Selecting {op['cell']} "
        f"using MultiportValve "
        f"port {op['cell_port']}..."
    )

    _select_cell(
        runner,
        op["cell_port"]
    )


    # ========================================================
    # 6. PREPARE ELECTROLYTE IN SPM+
    # ========================================================

    _prepare_spm_electrolyte(
        runner,
        op
    )


    # ========================================================
    # 7. SET PUMP 1 - MB
    # ========================================================

    pump1_uL_min = round(
        op["flow_mb_ml_min"]
        * 1000.0,
        0
    )

    print(
        f"Setting Pump 1 (MB) = "
        f"{pump1_uL_min:.0f} uL/min"
    )

    runner.opc.write_value(
        PUMP_MB_TAG,
        pump1_uL_min
    )


    # ========================================================
    # 8. SET PUMP 2 - WATER
    # ========================================================

    pump2_uL_min = round(
        op["flow_water_ml_min"]
        * 1000.0,
        0
    )

    print(
        f"Setting Pump 2 (water) = "
        f"{pump2_uL_min:.0f} uL/min"
    )

    runner.opc.write_value(
        PUMP_WATER_TAG,
        pump2_uL_min
    )


    # ========================================================
    # 9. START SPM+ ELECTROLYTE DELIVERY
    # ========================================================

    print(
        "Starting SPM+ electrolyte delivery..."
    )

    _start_spm_delivery(
        runner
    )


    # ========================================================
    # 10. FILL SELECTED CELL
    # ========================================================

    print(
        f"Filling {op['cell']} for "
        f"{op['filling_time_cell_s']:.1f} seconds..."
    )

    time.sleep(
        op["filling_time_cell_s"]
    )


    # ========================================================
    # 11. BKK CHANNEL SELECTION
    # ========================================================

    print(
        f"BKK channel required: "
        f"{op['bkk_channel']}"
    )

    # --------------------------------------------------------
    # TODO:
    #
    # Add actual BKK channel-selection command here once the
    # runner interface is confirmed.
    #
    # Example conceptual operation:
    #
    # runner.select_bkk_channel(
    #     op["bkk_channel"]
    # )
    # --------------------------------------------------------


    # ========================================================
    # 12. SET CURRENT
    # ========================================================

    print(
        f"Setting current = "
        f"{op['total_current_A']:.6f} A"
    )

    runner.set_current(
        op["total_current_A"]
    )


    # ========================================================
    # 13. POWER ON
    # ========================================================

    print(
        "Turning power supply ON..."
    )

    runner.turn_on_power_supply()


    # ========================================================
    # RETURN OPERATING POINT
    # ========================================================

    return op