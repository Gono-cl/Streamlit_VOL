import time


ECHEM_CELL_VOLUME_ML = 0.8
FILLING_MARGIN_FACTOR = 1.5


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
        "title": "PDA 5 variables ",
        "description": (
            "Running protocol for the optimization of the continuous process of PDA production from Hydrazone."
            " The set up require 4 pumps. "
            " P1 = base (imidazol). "
            " P2 = acid (TFA). "
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
        "flow_rate",
        "substrate_concentration",
        "acid_concentration",
        "base_concentration",
        "Voltage",
    ]


def prepare_hardware(runner, parameters):
    """
    Called before measurement in real/hybrid mode.
    Use runner methods (pump/valve/power helpers) to configure hardware.
    """
    flow_rate = float(parameters["flow_rate"])
    substrate = float(parameters["substrate_concentration"])
    acid = float(parameters["acid_concentration"])
    base = float(parameters["base_concentration"])
    voltage = float(parameters["Voltage"])
    if flow_rate <= 0:
        raise ValueError("flow_rate must be > 0 mL/min.")

    filling_time_cell_s = (ECHEM_CELL_VOLUME_ML / flow_rate) * 60.0 * FILLING_MARGIN_FACTOR
    runner.flow_electrochemical_cell_from_four_variables(flow_rate, substrate, acid, base)
    print(f"filling electrochemical cell for {filling_time_cell_s:.1f} seconds")
    _countdown_to_filling(runner, filling_time_cell_s)
    runner.set_voltage(voltage)
    runner.turn_on_power_supply()
    runner.countdown_echem(flow_rate)


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
    substrate = float(parameters.get("substrate_concentration", 1.0))
    return runner.process_adapter.calculate_real_result(
        runner,
        mean_measurement,
        {"substrate_concentration": substrate, **parameters},
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
    return runner.process_adapter.autosampler_flow_rate(runner, parameters)


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
