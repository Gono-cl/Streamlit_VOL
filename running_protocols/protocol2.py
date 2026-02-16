"""
Protocol 2 example.

Goal:
- Show how to implement a reaction-specific flow calculation directly in the
  protocol file.
- No change is required in core mixins for this to work.

How this works:
- The app loads this file and calls the functions below.
- `runner` is an ExperimentRunner instance passed in by the framework.
- You can call existing runner methods (pump writes, voltage, cleanup, etc.).
"""

from __future__ import annotations


def protocol_info():
    """Optional metadata used by the Running Protocol Library page."""
    return {
        "title": "Protocol 2 (custom flow split in file)",
        "description": (
            "Shows a reaction-specific flow model implemented directly in the "
            "protocol script, without editing mixins."
        ),
        "images": [
            {"path": "assets/example_scheme.svg", "caption": "Custom reaction scheme"},
        ],
    }


def required_parameter_keys():
    """
    Optional: used by DOE Executor to build a template.
    Keep this aligned with parameters used in prepare_hardware().
    """
    return [
        "total_flow_ml_min",
        "a_target_mM",
        "b_target_mM",
        "c_target_mM",
        "Voltage",
        "residence_time",
    ]


def _reaction2_split_flows(total_flow_ml_min, a_target_mM, b_target_mM, c_target_mM):
    """
    Reaction-specific flow model defined INSIDE the protocol file.

    This is exactly the part you were asking about: custom logic can live here.
    You do not need to add this function to flow_calculations_mixin unless you
    want to reuse it globally in many protocols.
    """
    # Example stock concentrations for this reaction
    a_stock_mM = 1200.0
    b_stock_mM = 800.0
    c_stock_mM = 1500.0

    flow_a = float(a_target_mM) * float(total_flow_ml_min) / a_stock_mM
    flow_b = float(b_target_mM) * float(total_flow_ml_min) / b_stock_mM
    flow_c = float(c_target_mM) * float(total_flow_ml_min) / c_stock_mM
    flow_solvent = float(total_flow_ml_min) - (flow_a + flow_b + flow_c)
    if flow_solvent < 0:
        raise ValueError("Computed solvent flow is negative. Check targets/total flow.")

    return {
        "flow_a_ml_min": flow_a,
        "flow_b_ml_min": flow_b,
        "flow_c_ml_min": flow_c,
        "flow_solvent_ml_min": flow_solvent,
    }


def prepare_hardware(runner, parameters):
    """
    Called by ExperimentRunner before measurement in real/hybrid mode.
    """
    total_flow = float(parameters["total_flow_ml_min"])
    a_target = float(parameters["a_target_mM"])
    b_target = float(parameters["b_target_mM"])
    c_target = float(parameters["c_target_mM"])
    voltage = float(parameters["Voltage"])

    flows = _reaction2_split_flows(total_flow, a_target, b_target, c_target)

    # Example OPC writes (replace tags with your actual setup)
    runner.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flows["flow_a_ml_min"] * 1000, 0))
    runner.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1", round(flows["flow_b_ml_min"] * 1000, 0))
    runner.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1", round(flows["flow_c_ml_min"] * 1000, 0))
    runner.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flows["flow_solvent_ml_min"] * 1000, 0))

    # Then use existing runner helpers as usual
    runner.set_voltage(voltage)
    runner.turn_on_power_supply()
    runner.countdown_echem(total_flow)


def calculate_real_result(runner, mean_measurement, parameters, objectives, directions):
    """
    Called by ExperimentRunner in real mode after collect_measurements().

    This example reuses existing objective calculation through process_adapter.
    You can also compute your own objective dict here if needed.
    """
    substrate = float(parameters.get("a_target_mM", 1.0))
    payload = {"substrate_concentration": substrate, **parameters}
    return runner.process_adapter.calculate_real_result(
        runner,
        mean_measurement,
        payload,
        objectives,
        directions,
    )


def autosampler_flow_rate(runner, parameters):
    """
    Optional. If omitted, default runner/process-adapter behavior is used.
    """
    return float(parameters["total_flow_ml_min"])


def cleanup(runner, parameters):
    """
    Called after each experiment.
    """
    runner.turn_off_power_supply()
    runner.stop_pumps()
    runner.cleaning_electrochemical_cell()
