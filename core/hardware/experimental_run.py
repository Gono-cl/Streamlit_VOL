"""
Experiment runner orchestration.

Delegates hardware control, measurement handling, UI helpers,
and flow math to mixin classes in core.hardware.mixins.*. The public interface
and import path (ExperimentRunner) remain the same for callers such as
Single_Objective.py and Multi_Objective.py.
"""
from __future__ import annotations

import csv
import os
import time
from collections.abc import Mapping
from datetime import datetime

import streamlit as st
from core.hardware.autosampler import AutoSampler
from core.hardware.mixins.echem_mixin import EchemMixin
from core.hardware.mixins.flow_calculations_mixin import FlowCalculationsMixin
from core.hardware.mixins.measurement_mixin import MeasurementMixin
from core.hardware.mixins.maintenance_mixin import MaintenanceMixin
from core.hardware.process_adapters import (
    DEFAULT_PROCESS_ADAPTER,
    create_process_adapter,
)
from core.hardware.protocol_scripts import load_protocol_module, protocol_info
from core.hardware.mixins.ui_mixin import UIMixin
from core.hardware.opc_communication import OPCClient
from core.objectives import calculate_objectives


class ExperimentRunner(
    FlowCalculationsMixin,
    MeasurementMixin,
    MaintenanceMixin,
    UIMixin,
    EchemMixin,
):
    def __init__(
        self,
        opc_client: OPCClient,
        csv_filename: str,
        simulation_mode: str = "off",
        use_autosampler: bool = False,
        volume_to_collect: float = 3.0,
        process_adapter: str | None = None,
        adapter_config: dict | None = None,
        running_protocol_script: str | None = None,
        measurement_source_prefix: str | None = None,
        measurement_source_signal: str | None = None,
        measurement_source_tag: str | None = None,
    ):
        self.opc = opc_client
        self.use_autosampler = use_autosampler
        self.autosampler = AutoSampler(opc_client, vial_volume_ml=2.0) if use_autosampler else None
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode  # Options: "off", "full", "hybrid"
        self.process_adapter_name = process_adapter or DEFAULT_PROCESS_ADAPTER
        self.adapter_config = dict(adapter_config or {})
        self.process_adapter = create_process_adapter(self.process_adapter_name, config=self.adapter_config)
        self.running_protocol_script = running_protocol_script or None
        self.running_protocol_module = None
        self.measurement_source_prefix = measurement_source_prefix
        self.measurement_source_signal = measurement_source_signal
        self.measurement_source_tag = measurement_source_tag
        if self.running_protocol_script:
            try:
                self.running_protocol_module = load_protocol_module(self.running_protocol_script)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load running protocol script '{self.running_protocol_script}': {exc}"
                ) from exc
            try:
                info = protocol_info(self.running_protocol_script)
            except Exception:
                info = {}
            if self.measurement_source_prefix is None:
                self.measurement_source_prefix = str(info.get("measurement_source_prefix", "")).strip() or None
            if self.measurement_source_signal is None:
                self.measurement_source_signal = str(info.get("measurement_source_signal", "")).strip() or None
            if self.measurement_source_tag is None:
                self.measurement_source_tag = str(info.get("measurement_source_tag", "")).strip() or None
        if self.measurement_source_prefix is None:
            self.measurement_source_prefix = "OpusOPCSvr.HP-CZC3484P17->"
        if self.measurement_source_signal is None:
            self.measurement_source_signal = "PDA - mM"
        self.protocol_prepare_fn = self._protocol_callable("prepare_hardware")
        self.protocol_calculate_fn = self._protocol_callable("calculate_real_result")
        self.protocol_autosampler_fn = self._protocol_callable("autosampler_flow_rate")
        self.protocol_stop_pumps_fn = self._protocol_callable("stop_pumps")
        self.protocol_cleanup_fn = self._protocol_callable("cleanup")
        self.protocol_measurement_tag_fn = self._protocol_callable("measurement_tag")
        self.experiment_status_placeholder = st.sidebar.empty()
        self.countdown_placeholder = st.empty()
        self.timer_placeholder = st.sidebar.empty()
        self.measurements_plot_placeholder = st.empty()
        self.start_time = None
        self.full_measurement_log = []  # Store all measurements for the full experiment
        self.tray_pos_waste = 0
        self.tray_pos_collect = 1
        self.volume_to_collect = volume_to_collect  # Volume to collect in mL

    def _protocol_callable(self, function_name: str):
        if self.running_protocol_module is None:
            return None
        fn = getattr(self.running_protocol_module, function_name, None)
        if callable(fn):
            return fn
        return None

    def _normalize_result_payload(self, result, objectives):
        """
        Normalize runner outputs to a flat dict {objective_name: float_value}.
        This avoids nested dict payloads (e.g. {"Yield": {"Yield": 42.0}}).
        """
        objective_names = list(objectives or [])

        if isinstance(result, Mapping):
            normalized = dict(result)
            if len(objective_names) == 1:
                key = objective_names[0]
                val = normalized.get(key)
                if isinstance(val, Mapping):
                    if key in val:
                        normalized[key] = val[key]
                    elif len(val) == 1:
                        normalized[key] = next(iter(val.values()))
                elif key not in normalized and len(normalized) == 1:
                    # Single-objective fallback: remap single returned value to requested objective key.
                    normalized[key] = next(iter(normalized.values()))
            return normalized

        if len(objective_names) == 1:
            return {objective_names[0]: result}
        return result

    # ---------------------------------------------------------------------------
    #                          STANDARD PROCESS FUNCTIONS
    # ---------------------------------------------------------------------------
    def init_csv(self):
        """Ensure the experiment CSV file exists."""
        if not self.csv_filename:
            return
        if not os.path.exists(self.csv_filename):
            os.makedirs(os.path.dirname(self.csv_filename) or ".", exist_ok=True)
            with open(self.csv_filename, "w", newline="") as _:
                pass

    def initialize_experiment(self, experiment_number, iterations, parameters):
        self.start_time = time.time()
        print(f"Running Experiment {experiment_number} of {iterations}")
        print(f"Parameters: {parameters}")
        if self.simulation_mode == "off":
            if not self.opc.check_connection("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1"):
                print("Connection failed. Aborting experiment.")
                return
        self.init_csv()

    # ---------------------------------------------------------------------------
    #                          SIMULATION HELPERS
    # ---------------------------------------------------------------------------
    def simulate_experiment(self, parameters, objectives=None, directions=None):
        if objectives is None:
            objectives = ["Normalized Area", "Throughput"]

        print("Simulating experiment...")
        reactor_volume = 1.4  # mL
        res_time = parameters.get("Voltage", 20)

        if self.simulation_mode in ["off"]:
            raw_area = self.collect_measurements(parameters=parameters)
        else:
            raw_area = self.synthetic_raw_area(res_time)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.full_measurement_log.append(
                {
                    "Iteration": parameters.get("iteration", 0),
                    "Timestamp": timestamp,
                    **parameters,
                    "Measurement #": 1,
                    "Value": raw_area,
                }
            )

        # Flow calculations for objectives
        flow_aq, flow_org, total_flow = self.calculate_flows(
            parameters.get("residence_time", 20), parameters.get("ratio_org_aq", 1.0)
        )
        res_time = parameters.get("residence_time", 20)
        total_flow = reactor_volume / (res_time / 60)
        flow_aq = total_flow / 2
        flow_org = total_flow - flow_aq

        result = calculate_objectives(
            raw_area,
            float(parameters.get("substrate_concentration", 1.0)),
            flow_aq,
            flow_org,
            res_time,
            selected_objectives=objectives,
            directions=directions,
        )
        return self._normalize_result_payload(result, objectives)

    # ---------------------------------------------------------------------------
    #                          RUNNING EXPERIMENT
    # ---------------------------------------------------------------------------
    def run_experiment(
        self,
        parameters,
        experiment_number=None,
        total_iterations=None,
        objectives=None,
        directions=None,
        status_title=None,
        status_note=None,
    ):
        should_display = status_title is not None or (
            experiment_number is not None and total_iterations is not None
        )
        if should_display:
            if status_title is not None:
                print(f"Running {status_title}")
                if status_note:
                    print(status_note)
            else:
                print(f"Running Experiment {experiment_number} of {total_iterations}")
            self.display_experiment_info(
                experiment_number,
                total_iterations,
                parameters,
                status_title=status_title,
                status_note=status_note,
            )

        if self.simulation_mode in ["off", "hybrid"]:
            if self.protocol_prepare_fn:
                self.protocol_prepare_fn(self, parameters)
            else:
                self.process_adapter.prepare_hardware(self, parameters)
        else:
            print("Full simulation mode enabled: skipping temperature and pump setup.")

        if self.simulation_mode in ["full"]:
            result = self.simulate_experiment(parameters, objectives, directions)
        else:
            mean_measurement = self.collect_measurements(parameters=parameters)
            if self.protocol_calculate_fn:
                result = self.protocol_calculate_fn(
                    self,
                    mean_measurement,
                    parameters,
                    objectives,
                    directions,
                )
            else:
                result = self.process_adapter.calculate_real_result(
                    self,
                    mean_measurement,
                    parameters,
                    objectives,
                    directions,
                )
            result = self._normalize_result_payload(result, objectives)

        if self.use_autosampler:
            self.autosampler.clean_before_collect(self.tray_pos_waste)
            self.autosampler.move_prepare_needle(self.tray_pos_collect)
            if self.protocol_autosampler_fn:
                flow_org = float(self.protocol_autosampler_fn(self, parameters))
            else:
                flow_org = self.process_adapter.autosampler_flow_rate(self, parameters)
            self.autosampler.start_collection(flow_rate=flow_org, volume=self.volume_to_collect)
            self.tray_pos_waste = (self.tray_pos_waste + 2) % 32
            self.tray_pos_collect = (self.tray_pos_collect + 2) % 32
        else:
            print("Autosampler disabled: skipping sample collection.")

        if self.protocol_stop_pumps_fn:
            self.protocol_stop_pumps_fn(self, parameters)

        if self.protocol_cleanup_fn:
            self.protocol_cleanup_fn(self, parameters)
        else:
            self.process_adapter.cleanup(self, parameters)
        return self._normalize_result_payload(result, objectives)

    # ---------------------------------------------------------------------------
    #                          SAVING FUNCTIONS
    # ---------------------------------------------------------------------------
    def save_full_measurements_to_csv(self, experiment_name):
        os.makedirs("raw_measurements", exist_ok=True)
        filename = f"raw_measurements/{experiment_name.replace(' ', '_')}_measurements.csv"
        keys = self.full_measurement_log[0].keys() if self.full_measurement_log else []
        file_exists = os.path.isfile(filename)
        mode = "a" if file_exists else "w"
        with open(filename, mode, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.full_measurement_log)
        print(f"Full measurement log saved to {filename}")
        self.full_measurement_log.clear()
        return filename
