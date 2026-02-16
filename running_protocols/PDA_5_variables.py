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

    runner.flow_electrochemical_cell_from_four_variables(flow_rate, substrate, acid, base)
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


def autosampler_flow_rate(runner, parameters):
    """Optional autosampler flow hook."""
    return runner.process_adapter.autosampler_flow_rate(runner, parameters)


def cleanup(runner, parameters):
    """Called after each experiment."""
    runner.process_adapter.cleanup(runner, parameters)
