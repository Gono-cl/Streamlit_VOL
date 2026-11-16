from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
import threading


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
# port 1 -> Na2SO4
# port 2 -> NaCl
# port 3 -> NaNO3
# port 4 -> reactor delivery
#
# Current stock concentration = 0.4 M

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
# SPM+ SETTINGS
# ============================================================

# IMPORTANT:
# SPMPump class uses uL and uL/min.

SPM_LOADING_FLOW_UL_MIN = 5000.0

# Full 5 mL syringe
SPM_LOAD_VOLUME_UL = 5000.0

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


# ============================================================
# BKK CHANNEL TAGS
# ============================================================

BKK_CHANNEL_TAGS = {
    1: "BKK_USER_DEFINE.CHANNEL1",
    2: "BKK_USER_DEFINE.CHANNEL2",
    3: "BKK_USER_DEFINE.CHANNEL3",
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

def _select_cell(runner, cell_port):
    """
    Move external AMF MultiportValve to selected cell.
    """

    if not hasattr(runner, "cell_valve") or runner.cell_valve is None:
        raise RuntimeError(
            "runner.cell_valve is not configured."
        )

    runner.cell_valve.move_to(
        cell_port
    )


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

def _prepare_spm_electrolyte(
    runner,
    op
):
    """
    Load selected electrolyte into SPM+.

    Sequence
    --------
    1. Select electrolyte source port.
    2. Set loading flow in uL/min.
    3. Aspirate 5000 uL.
    4. Switch to delivery port 4.
    5. Set calculated delivery flow in uL/min.
    """

    if not hasattr(runner, "spm"):
        raise RuntimeError(
            "runner.spm is not configured."
        )

    spm = runner.spm

    electrolyte_port = (
        op["electrolyte_spm_port"]
    )

    electrolyte_flow_uL_min = (
        op["flow_electrolyte_uL_min"]
    )

    print(
        f"SPM+: selecting electrolyte source "
        f"port {electrolyte_port}"
    )

    spm.move_valve(
        electrolyte_port
    )

    print(
        f"SPM+: loading at "
        f"{SPM_LOADING_FLOW_UL_MIN:.0f} uL/min"
    )

    spm.set_flow_rate(
        SPM_LOADING_FLOW_UL_MIN
    )

    print(
        f"SPM+: aspirating "
        f"{SPM_LOAD_VOLUME_UL:.0f} uL"
    )

    spm.pickup(
        SPM_LOAD_VOLUME_UL
    )

    print(
        f"SPM+: switching to delivery "
        f"port {SPM_DELIVERY_PORT}"
    )

    spm.move_valve(
        SPM_DELIVERY_PORT
    )

    print(
        f"SPM+: delivery flow = "
        f"{electrolyte_flow_uL_min:.1f} uL/min "
        f"({op['flow_electrolyte_ml_min']:.4f} mL/min)"
    )

    spm.set_flow_rate(
        electrolyte_flow_uL_min
    )


# ============================================================
# START SPM+ DELIVERY
# ============================================================

def _start_spm_delivery(runner):
    """
    Start SPM+ electrolyte delivery in a separate thread.

    SPMPump.dispense() is blocking, so the dispensing command
    must run independently while the main protocol continues.
    """

    def _dispense():
        try:
            runner.spm.dispense(
                SPM_LOAD_VOLUME_UL
            )
        except Exception as exc:
            print(
                f"SPM+ dispensing thread error: {exc}"
            )

    thread = threading.Thread(
        target=_dispense,
        daemon=True
    )

    thread.start()

    print(
        "SPM+ dispensing started in background."
    )

    return thread


# ============================================================
# STOP SPM+
# ============================================================

def _stop_spm_delivery(runner):
    """
    Stop SPM+ immediately, leaving the remaining liquid
    inside the syringe.
    """

    if runner.spm is None:
        return

    try:
        runner.spm.hard_stop()

        print(
            "SPM+ electrolyte delivery stopped."
        )

    except Exception as exc:
        print(
            f"Warning: could not stop SPM+: {exc}"
        )


# ============================================================
# SAFE SHUTDOWN
# ============================================================

def emergency_shutdown(runner):
    """
    Best-effort safe shutdown.

    Called if hardware preparation fails and can also be
    called by the runner after other experiment errors.
    """

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
        runner
    )

    print("!!! SAFE SHUTDOWN COMPLETE !!!")
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
        f"Cell port             = {op['cell_port']}"
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
    4. Ensure PSU OFF.
    5. Select electrochemical cell.
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

        # ====================================================
        # 1. POWER SUPPLY OFF
        # ====================================================

        print(
            "Ensuring power supply is OFF..."
        )

        runner.turn_off_power_supply()


        # ====================================================
        # 2. SELECT CELL
        # ====================================================

        print(
            f"Selecting {op['cell']} "
            f"on valve port "
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

        print(
            f"ERROR during hardware preparation: {exc}"
        )

        _log_operating_point(
            op,
            status="hardware_error",
            error_message=str(exc)
        )

        emergency_shutdown(
            runner
        )

        raise


# ============================================================
# NORMAL SHUTDOWN / END OF EXPERIMENT
# ============================================================

def stop_pumps(runner, parameters):
    """
    Safely stop electrochemistry and all active feed pumps
    after the experiment.

    Shutdown order:
        1. Power supply OFF
        2. SPM+ electrolyte delivery OFF
        3. Pump 1 MB OFF
        4. Pump 2 water OFF
        5. Pump 3 analytical dilution OFF
    """

    # --------------------------------------------------------
    # 1. POWER SUPPLY OFF
    # --------------------------------------------------------

    try:
        runner.turn_off_power_supply()
        print("Power supply OFF.")

    except Exception as exc:
        print(
            f"Warning: power supply shutdown failed: {exc}"
        )


    # --------------------------------------------------------
    # 2. SPM+ OFF
    # --------------------------------------------------------

    _stop_spm_delivery(
        runner
    )


    # --------------------------------------------------------
    # 3. PUMP 1 — MB OFF
    # --------------------------------------------------------

    try:
        runner.opc.write_value(
            PUMP_MB_TAG,
            0
        )

        print("Pump 1 MB stopped.")

    except Exception as exc:
        print(
            f"Warning: Pump 1 stop failed: {exc}"
        )


    # --------------------------------------------------------
    # 4. PUMP 2 — WATER OFF
    # --------------------------------------------------------

    try:
        runner.opc.write_value(
            PUMP_WATER_TAG,
            0
        )

        print("Pump 2 water stopped.")

    except Exception as exc:
        print(
            f"Warning: Pump 2 stop failed: {exc}"
        )


    # --------------------------------------------------------
    # 5. PUMP 3 — ANALYTICAL DILUTION OFF
    # --------------------------------------------------------

    try:
        runner.opc.write_value(
            PUMP_ANALYTICAL_DILUTION_TAG,
            0
        )

        print("Pump 3 analytical dilution stopped.")

    except Exception as exc:
        print(
            f"Warning: Pump 3 stop failed: {exc}"
        )

    print(
        "Experiment hardware stopped."
    )

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

def cleanup(
    runner,
    parameters
):
    """
    Placeholder.

    Cleaning sequence still needs to be experimentally defined.

    Likely future logic:

    - PSU OFF
    - stop reaction feeds
    - discard remaining SPM electrolyte
    - rinse SPM syringe / internal valve
    - flush reaction mixer/tubing
    - flush electrochemical cell
    - prepare next electrolyte/cell

    Do NOT return residual electrolyte to stock bottle
    unless contamination risk has been experimentally excluded.
    """

    if runner.simulation_mode not in [
        "off",
        "hybrid",
    ]:

        print(
            "Simulation mode: "
            "skipping cleaning."
        )

        return

    print(
        "Cleaning protocol not yet defined."
    )