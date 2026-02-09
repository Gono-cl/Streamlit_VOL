"""Process adapter abstractions for ExperimentRunner hardware behavior."""

from __future__ import annotations

import time
from typing import Any

from core.objectives import calculate_objectives


VALVE_REACTION_TAGS = (
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0),
)

DEFAULT_ECHEM_PUMP_TAGS = {
    "base": "Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1",
    "acid": "Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1",
    "solvent": "Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1",
    "substrate": "Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1",
}

DEFAULT_PROCESS_ADAPTER = "echem_4pump"


class BaseProcessAdapter:
    """Defines process-specific behavior used by ExperimentRunner."""

    name = "base"
    label = "Base Adapter"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})

    def prepare_hardware(self, runner, parameters: dict[str, Any]) -> None:
        """Set pump/valve/power states before measurement."""
        return

    def required_parameter_keys(self) -> list[str]:
        """Return parameter keys required by prepare_hardware for this adapter."""
        return []

    def preview_prepare(self, parameters: dict[str, Any]) -> dict:
        """
        Return a serializable preview of prepare_hardware behavior.
        Subclasses should override to expose adapter-specific actions and equations.
        """
        return {
            "adapter": self.name,
            "required_keys": self.required_parameter_keys(),
            "resolved_values": {},
            "computed": {},
            "actions": [],
            "warnings": [],
        }

    def calculate_real_result(self, runner, mean_measurement: float, parameters: dict[str, Any], objectives, directions):
        """Compute objective values from measured data in real-hardware mode."""
        substrate_key = str(self.config.get("substrate_key", "substrate_concentration"))
        substrate_concentration = self._require_float(parameters, substrate_key)
        return calculate_objectives(
            mean_measurement,
            substrate_concentration,
            selected_objectives=objectives,
            directions=directions,
        )

    def autosampler_flow_rate(self, runner, parameters: dict[str, Any]) -> float:
        """Return collection flow rate used by autosampler."""
        residence_time_key = str(self.config.get("residence_time_key", "residence_time"))
        residence_time = self._require_float(parameters, residence_time_key)
        return float(runner.calculate_flows1(residence_time))

    def cleanup(self, runner, parameters: dict[str, Any]) -> None:
        """Reset hardware after each experiment."""
        runner.turn_off_power_supply()
        runner.stop_pumps()
        runner.cleaning_electrochemical_cell()

    @staticmethod
    def _require_float(parameters: dict[str, Any], key: str) -> float:
        if key not in parameters:
            raise KeyError(f"Missing required parameter '{key}' for process adapter.")
        return float(parameters[key])


class EchemFourPumpAdapter(BaseProcessAdapter):
    """
    Legacy behavior currently used in the project:
    - target concentrations for substrate/acid/base
    - 4 pumps (3 reagents + 1 solvent)
    - fixed filling volume + voltage control
    """

    name = "echem_4pump"
    label = "E-chem 4-pump (legacy)"

    def _resolved_keys(self) -> dict[str, str]:
        return {
            "flow_rate_key": str(self.config.get("flow_rate_key", "flow_rate")),
            "substrate_key": str(self.config.get("substrate_key", "substrate_concentration")),
            "acid_key": str(self.config.get("acid_key", "acid_concentration")),
            "base_key": str(self.config.get("base_key", "base_concentration")),
            "voltage_key": str(self.config.get("voltage_key", "Voltage")),
            "residence_time_key": str(self.config.get("residence_time_key", "residence_time")),
        }

    def required_parameter_keys(self) -> list[str]:
        keys = self._resolved_keys()
        return [
            keys["flow_rate_key"],
            keys["substrate_key"],
            keys["acid_key"],
            keys["base_key"],
            keys["voltage_key"],
            keys["residence_time_key"],
        ]

    def prepare_hardware(self, runner, parameters: dict[str, Any]) -> None:
        keys = self._resolved_keys()
        flow_rate_key = keys["flow_rate_key"]
        substrate_key = keys["substrate_key"]
        acid_key = keys["acid_key"]
        base_key = keys["base_key"]
        voltage_key = keys["voltage_key"]
        filling_volume_ml = float(self.config.get("filling_volume_ml", 0.8))

        flow_rate = self._require_float(parameters, flow_rate_key)
        substrate_conc = self._require_float(parameters, substrate_key)
        acid_conc = self._require_float(parameters, acid_key)
        base_conc = self._require_float(parameters, base_key)
        voltage = self._require_float(parameters, voltage_key)

        runner.flow_electrochemical_cell_from_four_variables(flow_rate, substrate_conc, acid_conc, base_conc)
        filling_time = round(filling_volume_ml / flow_rate * 1.5 * 60, 2)
        print(f"filling the electrochemical cell for {filling_time} seconds ")
        time.sleep(max(0.0, filling_time))
        runner.set_voltage(voltage)
        runner.turn_on_power_supply()
        runner.countdown_echem(flow_rate)

    def preview_prepare(self, parameters: dict[str, Any]) -> dict:
        keys = self._resolved_keys()
        flow_rate = self._require_float(parameters, keys["flow_rate_key"])
        substrate_conc = self._require_float(parameters, keys["substrate_key"])
        acid_conc = self._require_float(parameters, keys["acid_key"])
        base_conc = self._require_float(parameters, keys["base_key"])
        voltage = self._require_float(parameters, keys["voltage_key"])
        filling_volume_ml = float(self.config.get("filling_volume_ml", 0.8))

        stock_substrate_concentration = 716.0
        stock_acid_concentration = 1500.0
        stock_base_concentration = 1500.0

        flow_sub = substrate_conc * flow_rate / stock_substrate_concentration
        flow_acid = acid_conc * flow_rate / stock_acid_concentration
        flow_base = base_conc * flow_rate / stock_base_concentration
        flow_solvent = flow_rate - (flow_sub + flow_acid + flow_base)
        filling_time = round(filling_volume_ml / flow_rate * 1.5 * 60, 2)

        warnings = []
        if flow_solvent < 0:
            warnings.append("Computed solvent flow is negative. Check concentrations/total flow.")

        pump_writes = {
            DEFAULT_ECHEM_PUMP_TAGS["base"]: round(flow_base * 1000, 0),
            DEFAULT_ECHEM_PUMP_TAGS["acid"]: round(flow_acid * 1000, 0),
            DEFAULT_ECHEM_PUMP_TAGS["solvent"]: round(max(0.0, flow_solvent) * 1000, 0),
            DEFAULT_ECHEM_PUMP_TAGS["substrate"]: round(flow_sub * 1000, 0),
        }

        return {
            "adapter": self.name,
            "required_keys": self.required_parameter_keys(),
            "resolved_values": {
                "flow_rate": flow_rate,
                "substrate_concentration": substrate_conc,
                "acid_concentration": acid_conc,
                "base_concentration": base_conc,
                "voltage": voltage,
                "filling_volume_ml": filling_volume_ml,
            },
            "computed": {
                "flow_sub_ml_min": flow_sub,
                "flow_acid_ml_min": flow_acid,
                "flow_base_ml_min": flow_base,
                "flow_solvent_ml_min": flow_solvent,
                "filling_time_seconds": filling_time,
                "pump_writes_ul_min": pump_writes,
            },
            "actions": [
                "Set reaction valves to reaction position",
                "Write PUMP1(base), PUMP2(acid), PUMP3(solvent), PUMP4(substrate) setpoints",
                f"Wait filling time: {filling_time} s",
                f"Set voltage to {voltage}",
                "Turn on power supply",
                f"Start countdown using flow_rate {flow_rate}",
            ],
            "warnings": warnings,
        }


class EchemThreePumpAdapter(BaseProcessAdapter):
    """
    Flexible 3-reagent split adapter:
    - computes reagent flows from target concentration + stock concentration
    - supports custom parameter names and pump tags via adapter config
    - solvent takes the remaining flow
    """

    name = "echem_3pump"
    label = "E-chem 3-reagent split"

    def _resolved_keys(self) -> dict[str, str]:
        return {
            "flow_rate_key": str(self.config.get("flow_rate_key", "flow_rate")),
            "voltage_key": str(self.config.get("voltage_key", "Voltage")),
            "residence_time_key": str(self.config.get("residence_time_key", "residence_time")),
            "substrate_key": str(self.config.get("substrate_key", "substrate_concentration")),
        }

    def required_parameter_keys(self) -> list[str]:
        keys = self._resolved_keys()
        required = [keys["flow_rate_key"], keys["voltage_key"], keys["residence_time_key"]]
        components = self.config.get(
            "components",
            [
                {"name": "substrate", "param": "substrate_concentration", "stock_concentration": 716.0},
                {"name": "acid", "param": "acid_concentration", "stock_concentration": 1500.0},
                {"name": "base", "param": "base_concentration", "stock_concentration": 1500.0},
            ],
        )
        for comp in components:
            param_key = str(comp.get("param", ""))
            if param_key and param_key not in required:
                required.append(param_key)
        return required

    def prepare_hardware(self, runner, parameters: dict[str, Any]) -> None:
        keys = self._resolved_keys()
        flow_rate_key = keys["flow_rate_key"]
        voltage_key = keys["voltage_key"]
        filling_volume_ml = float(self.config.get("filling_volume_ml", 0.8))

        components = self.config.get(
            "components",
            [
                {"name": "substrate", "param": "substrate_concentration", "stock_concentration": 716.0},
                {"name": "acid", "param": "acid_concentration", "stock_concentration": 1500.0},
                {"name": "base", "param": "base_concentration", "stock_concentration": 1500.0},
            ],
        )
        pump_tags = dict(DEFAULT_ECHEM_PUMP_TAGS)
        pump_tags.update(self.config.get("pump_tags", {}))

        flow_rate = self._require_float(parameters, flow_rate_key)
        voltage = self._require_float(parameters, voltage_key)

        for tag, value in VALVE_REACTION_TAGS:
            runner.opc.write_value(tag, value)

        total_reagent_flow = 0.0
        for comp in components:
            comp_name = str(comp.get("name", "component"))
            param_key = str(comp.get("param", f"{comp_name}_concentration"))
            stock_conc = float(comp.get("stock_concentration", 1.0))
            pump_tag = str(comp.get("pump_tag", pump_tags.get(comp_name, "")))
            if not pump_tag:
                raise KeyError(f"Missing pump tag for component '{comp_name}'.")
            target_conc = self._require_float(parameters, param_key)
            flow_ml_min = target_conc * flow_rate / stock_conc
            total_reagent_flow += flow_ml_min
            runner.opc.write_value(pump_tag, round(flow_ml_min * 1000, 0))

        solvent_flow = flow_rate - total_reagent_flow
        solvent_tag = str(self.config.get("solvent_pump_tag", pump_tags["solvent"]))
        runner.opc.write_value(solvent_tag, round(max(0.0, solvent_flow) * 1000, 0))

        filling_time = round(filling_volume_ml / flow_rate * 1.5 * 60, 2)
        print(f"filling the electrochemical cell for {filling_time} seconds ")
        time.sleep(max(0.0, filling_time))
        runner.set_voltage(voltage)
        runner.turn_on_power_supply()
        runner.countdown_echem(flow_rate)

    def preview_prepare(self, parameters: dict[str, Any]) -> dict:
        keys = self._resolved_keys()
        flow_rate = self._require_float(parameters, keys["flow_rate_key"])
        voltage = self._require_float(parameters, keys["voltage_key"])
        filling_volume_ml = float(self.config.get("filling_volume_ml", 0.8))

        components = self.config.get(
            "components",
            [
                {"name": "substrate", "param": "substrate_concentration", "stock_concentration": 716.0},
                {"name": "acid", "param": "acid_concentration", "stock_concentration": 1500.0},
                {"name": "base", "param": "base_concentration", "stock_concentration": 1500.0},
            ],
        )
        pump_tags = dict(DEFAULT_ECHEM_PUMP_TAGS)
        pump_tags.update(self.config.get("pump_tags", {}))

        total_reagent_flow = 0.0
        component_flows = []
        pump_writes = {}
        for comp in components:
            comp_name = str(comp.get("name", "component"))
            param_key = str(comp.get("param", f"{comp_name}_concentration"))
            stock_conc = float(comp.get("stock_concentration", 1.0))
            pump_tag = str(comp.get("pump_tag", pump_tags.get(comp_name, "")))
            target_conc = self._require_float(parameters, param_key)
            flow_ml_min = target_conc * flow_rate / stock_conc
            total_reagent_flow += flow_ml_min
            component_flows.append(
                {
                    "name": comp_name,
                    "param_key": param_key,
                    "target_concentration": target_conc,
                    "stock_concentration": stock_conc,
                    "flow_ml_min": flow_ml_min,
                    "pump_tag": pump_tag,
                }
            )
            pump_writes[pump_tag] = round(flow_ml_min * 1000, 0)

        solvent_flow = flow_rate - total_reagent_flow
        solvent_tag = str(self.config.get("solvent_pump_tag", pump_tags["solvent"]))
        pump_writes[solvent_tag] = round(max(0.0, solvent_flow) * 1000, 0)
        filling_time = round(filling_volume_ml / flow_rate * 1.5 * 60, 2)

        warnings = []
        if solvent_flow < 0:
            warnings.append("Computed solvent flow is negative. Check concentrations/total flow.")

        return {
            "adapter": self.name,
            "required_keys": self.required_parameter_keys(),
            "resolved_values": {
                "flow_rate": flow_rate,
                "voltage": voltage,
                "filling_volume_ml": filling_volume_ml,
                "solvent_pump_tag": solvent_tag,
            },
            "computed": {
                "components": component_flows,
                "total_reagent_flow_ml_min": total_reagent_flow,
                "solvent_flow_ml_min": solvent_flow,
                "filling_time_seconds": filling_time,
                "pump_writes_ul_min": pump_writes,
            },
            "actions": [
                "Set reaction valves to reaction position",
                "Write configured reagent pump setpoints from concentration split",
                f"Write solvent pump setpoint to {solvent_tag}",
                f"Wait filling time: {filling_time} s",
                f"Set voltage to {voltage}",
                "Turn on power supply",
                f"Start countdown using flow_rate {flow_rate}",
            ],
            "warnings": warnings,
        }


PROCESS_ADAPTER_REGISTRY = {
    EchemFourPumpAdapter.name: EchemFourPumpAdapter,
    EchemThreePumpAdapter.name: EchemThreePumpAdapter,
}

PROCESS_ADAPTER_LABELS = {
    adapter_name: adapter_cls.label for adapter_name, adapter_cls in PROCESS_ADAPTER_REGISTRY.items()
}


def available_process_adapters() -> list[str]:
    return list(PROCESS_ADAPTER_REGISTRY.keys())


def create_process_adapter(process_adapter: str | BaseProcessAdapter | None, config: dict | None = None) -> BaseProcessAdapter:
    if isinstance(process_adapter, BaseProcessAdapter):
        return process_adapter

    adapter_name = process_adapter or DEFAULT_PROCESS_ADAPTER
    adapter_cls = PROCESS_ADAPTER_REGISTRY.get(adapter_name)
    if not adapter_cls:
        valid = ", ".join(PROCESS_ADAPTER_REGISTRY.keys())
        raise ValueError(f"Unknown process adapter '{adapter_name}'. Available: {valid}")
    return adapter_cls(config=config)
