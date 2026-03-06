import time


FARADAY_CONSTANT = 96485.33212  # C/mol
ELECTRODE_AREA_CM2 = 17.25
STOCK_SUBSTRATE_CONCENTRATION_MM = 766.0
STOCK_SALT_CONCENTRATION_MM = 1500.0
STOCK_BASE_CONCENTRATION_MM = 1500.0
ECHEM_CELL_VOLUME_ML = 0.8
FILLING_MARGIN_FACTOR = 1.5

VALVE_REACTION_TAGS = (
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0),
)
PUMP_BASE_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1"
PUMP_SALT_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1"
PUMP_SOLVENT_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1"
PUMP_SUBSTRATE_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1"


def _countdown_to_filling(runner, seconds):
    secs_total = max(0, int(round(float(seconds))))
    if secs_total <= 0:
        return
    for secs in range(secs_total, 0, -1):
        mm, ss = divmod(secs, 60)
        html = f"""
        <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
            <h4 style='margin:0;'>Countdown for filling the electrochemical cell</h4>
            <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
        </div>
        """
        runner.countdown_placeholder.markdown(html, unsafe_allow_html=True)
        time.sleep(1)


def protocol_info():
    """Optional metadata used by the Running Protocol Library page."""
    return {
        "title": "PDA 5 variables galvanostatic",
        "description": (
            "Running protocol for the optimization of the continuous process of PDA production from Hydrazone."
            " The set up requires 4 pumps with galvanostatic control. "
            " Total current is derived from current density and electrode area. "
            " Total flow is derived from applied charge and target substrate concentration. "
            " P1 = base (imidazol). "
            " P2 = salt (acid + base). "
            " P3 = acetonitrile. "
            " P4 = Substrate (Hydrazone). "
        ),
        "images": [
            {"path": "assets/example_scheme.svg", "caption": "PDA production setup scheme"},
        ],
        "measurement_source_prefix": "OpusOPCSvr.HP-CZC3484P17->",
        "measurement_source_signal": "PDA - mM",
    }


def required_parameter_keys():
    """Used by DOE Executor template generation (optional)."""
    return [
        "current_density",
        "applied_charge",
        "base_concentration",
        "salt_concentration",
        "substrate_concentration",
    ]


def _compute_operating_point(parameters):
    current_density_mA_cm2 = float(parameters["current_density"])
    applied_charge = float(parameters["applied_charge"])
    base_target_mM = float(parameters["base_concentration"])
    salt_target_mM = float(parameters["salt_concentration"])
    substrate_target_mM = float(parameters["substrate_concentration"])

    if current_density_mA_cm2 <= 0:
        raise ValueError("current_density must be > 0 mA/cm2.")
    if applied_charge <= 0:
        raise ValueError("applied_charge must be > 0 F/mol.")
    if substrate_target_mM <= 0:
        raise ValueError("substrate_concentration must be > 0 mM.")
    if base_target_mM < 0:
        raise ValueError("base_concentration must be >= 0 mM.")
    if salt_target_mM < 0:
        raise ValueError("salt_concentration must be >= 0 mM.")

    total_current_mA = current_density_mA_cm2 * ELECTRODE_AREA_CM2
    total_current_A = total_current_mA / 1000.0

    # Q[mL/min] = I[A] / (F * charge[F/mol]) * 60[s/min] * 1e6 / C[mM]
    total_flow_ml_min = (
        (total_current_A / FARADAY_CONSTANT / applied_charge)
        * 60.0
        * 1e6
        / substrate_target_mM
    )
    if total_flow_ml_min <= 0:
        raise ValueError("Computed total flow is <= 0 mL/min. Check inputs.")

    flow_pump4_substrate_ml_min = (substrate_target_mM / STOCK_SUBSTRATE_CONCENTRATION_MM) * total_flow_ml_min
    flow_pump2_salt_ml_min = (salt_target_mM / STOCK_SALT_CONCENTRATION_MM) * total_flow_ml_min
    flow_pump1_base_ml_min = (base_target_mM / STOCK_BASE_CONCENTRATION_MM) * total_flow_ml_min
    flow_pump3_solvent_ml_min = total_flow_ml_min - (
        flow_pump1_base_ml_min + flow_pump2_salt_ml_min + flow_pump4_substrate_ml_min
    )

    if flow_pump4_substrate_ml_min < 0:
        raise ValueError("Computed substrate pump flow is negative. Check substrate_concentration.")
    if flow_pump2_salt_ml_min < 0:
        raise ValueError("Computed salt pump flow is negative. Check salt_concentration.")
    if flow_pump1_base_ml_min < 0:
        raise ValueError("Computed base pump flow is negative. Check base_concentration.")
    if flow_pump3_solvent_ml_min < 0:
        raise ValueError("Computed solvent flow is negative. Check concentration constraints versus stock solutions.")

    filling_time_cell_s = (ECHEM_CELL_VOLUME_ML / total_flow_ml_min) * 60.0 * FILLING_MARGIN_FACTOR

    return {
        "current_density_mA_cm2": current_density_mA_cm2,
        "applied_charge": applied_charge,
        "substrate_target_mM": substrate_target_mM,
        "base_target_mM": base_target_mM,
        "salt_target_mM": salt_target_mM,
        "total_current_mA": total_current_mA,
        "total_current_A": total_current_A,
        "total_flow_ml_min": total_flow_ml_min,
        "flow_pump1_base_ml_min": flow_pump1_base_ml_min,
        "flow_pump2_salt_ml_min": flow_pump2_salt_ml_min,
        "flow_pump3_solvent_ml_min": flow_pump3_solvent_ml_min,
        "flow_pump4_substrate_ml_min": flow_pump4_substrate_ml_min,
        "filling_time_cell_s": filling_time_cell_s,
    }


def prepare_hardware(runner, parameters):
    """
    Called before measurement in real/hybrid mode.
    Use runner methods (pump/valve/power helpers) to configure hardware.
    """
    op = _compute_operating_point(parameters)

    print("PDA 4-pump galvanostatic protocol:")
    print(f"  current density = {op['current_density_mA_cm2']:.3f} mA/cm2")
    print(f"  total current   = {op['total_current_mA']:.3f} mA ({op['total_current_A']:.5f} A)")
    print(f"  applied charge  = {op['applied_charge']:.3f} F/mol")
    print(f"  target [Sub]    = {op['substrate_target_mM']:.3f} mM")
    print(f"  target [Salt]   = {op['salt_target_mM']:.3f} mM")
    print(f"  target [Base]   = {op['base_target_mM']:.3f} mM")
    print(f"  total flow      = {op['total_flow_ml_min']:.4f} mL/min")
    print(f"  pump1 (base)    = {op['flow_pump1_base_ml_min']:.4f} mL/min")
    print(f"  pump2 (salt)    = {op['flow_pump2_salt_ml_min']:.4f} mL/min")
    print(f"  pump3 (ACN)     = {op['flow_pump3_solvent_ml_min']:.4f} mL/min")
    print(f"  pump4 (substr.) = {op['flow_pump4_substrate_ml_min']:.4f} mL/min")
    print(f"  t_fill_cell     = {op['filling_time_cell_s']:.1f} s")

    if runner.simulation_mode not in ["off", "hybrid"]:
        print("Full simulation mode: skipping hardware setup.")
        return

    for tag, value in VALVE_REACTION_TAGS:
        runner.opc.write_value(tag, value)

    runner.opc.write_value(PUMP_BASE_TAG, round(op["flow_pump1_base_ml_min"] * 1000.0, 0))
    runner.opc.write_value(PUMP_SALT_TAG, round(op["flow_pump2_salt_ml_min"] * 1000.0, 0))
    runner.opc.write_value(PUMP_SOLVENT_TAG, round(op["flow_pump3_solvent_ml_min"] * 1000.0, 0))
    runner.opc.write_value(PUMP_SUBSTRATE_TAG, round(op["flow_pump4_substrate_ml_min"] * 1000.0, 0))

    print(f"filling electrochemical cell for {op['filling_time_cell_s']:.1f} seconds")
    _countdown_to_filling(runner, op["filling_time_cell_s"])
    runner.set_current(op["total_current_A"])
    runner.turn_on_power_supply()
    runner.countdown_echem(op["total_flow_ml_min"])


def measurement_tag(runner, parameters):
    """
    Optional hook for measurement source.
    Return the full OPC tag that should be read for this protocol.
    """
    return "OpusOPCSvr.HP-CZC3484P17->PDA - mM"


def calculate_real_result(runner, mean_measurement, parameters, objectives, directions):
    """
    Called after measurement in real mode.
    Return a dict like: {"Yield": 51.2, "Throughput": 120.0}
    """
    op = _compute_operating_point(parameters)
    return runner.process_adapter.calculate_real_result(
        runner,
        mean_measurement,
        {"substrate_concentration": op["substrate_target_mM"], **parameters},
        objectives,
        directions,
    )

def stop_pumps(runner, parameters):
    if runner.simulation_mode in ["off", "hybrid"]:
        for pump in ["PUMP1.W1", "PUMP2.W1", "PUMP3.W1", "PUMP4.W1", "PC_OUT"]:
            runner.opc.write_value(f"Hitec_OPC_DA20_Server->E_CHEM:{pump}", 0)
        print("All pumps stopped.")
        runner.turn_off_power_supply()
        time.sleep(10)
    else:
        print("Simulation mode: skipping pump shutdown.")

def autosampler_flow_rate(runner, parameters):
    """Optional autosampler flow hook."""
    op = _compute_operating_point(parameters)
    return float(op["total_flow_ml_min"])


def cleanup(runner, parameters):
    if runner.simulation_mode not in ["off", "hybrid"]:
        print("Simulation mode: skipping cleaning step.")
        return

    # ACN Cell Cleaning
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0)
    print("Valves switched to cleaning position.")
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 5)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 3)
    print("Cleaning electrochemical cell with TFA 10%...")
    time.sleep(60)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
    time.sleep(5)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 4)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 3)
    print("Cleaning electrochemical cell with solvent ACN...")
    time.sleep(60)
    runner.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
    time.sleep(5)
    print("Cleaning Complete.")
