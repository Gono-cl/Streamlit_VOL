"""
Experiment runner orchestration.

Delegates hardware control, measurement handling, UI helpers,
and flow math to mixin classes in core.hardware.mixins.*. The public interface
and import path (ExperimentRunner) remain the same for callers such as
Single_Objective.py and Multi_Objective.py.
"""
from __future__ import annotations

import csv
import math
import os
import tempfile
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
from core.hardware.uvvis_measurement import measurement_settings
from core.hardware.AMF_hardware.multiport_valve import MultiportValve
from core.hardware.AMF_hardware.spm_pump import SPMPump
from core.hardware.connections import close_runner, disconnect_devices, disconnect_on_error


# ---------------------------------------------------------------------------
# AMF LABVISION DISPLAY TAGS
# ---------------------------------------------------------------------------

SPM_FLOW_TAG = "Hitec_OPC_DA20_Server->E_CHEM:AMF_PUMP_FLOW"
SPM_INTERNAL_VALVE_TAG = "Hitec_OPC_DA20_Server->E_CHEM:AMF_SPM_MULTIPORT"
CELL_MULTIPORT_TAG = "Hitec_OPC_DA20_Server->E_CHEM:AMF_MULTIPORT_VALVE"
AMF_DISPLAY_TIMEOUT_S = 1.0


class ExperimentRunner(
    FlowCalculationsMixin,
    MeasurementMixin,
    MaintenanceMixin,
    UIMixin,
    EchemMixin,
):
    @disconnect_on_error
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
        measurement_backend: str = "opc",
        uvvis_config: dict | None = None,
    ):
        self._closed = False
        self.opc = opc_client
        self.use_autosampler = use_autosampler
        self.autosampler = AutoSampler(opc_client, vial_volume_ml=2.0) if use_autosampler else None
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode  # Options: "off", "full", "hybrid"

        # ------------------------------------------------------------
        # AMF HARDWARE
        # ------------------------------------------------------------

        self.cell_valve = None
        self.spm = None

        if self.simulation_mode in ["off", "hybrid"]:

            print("Initializing AMF hardware...")

            self.cell_valve = MultiportValve(
                auto_home=True,
                max_port=12
            )

            self.spm = SPMPump(
                # Let the selected protocol home once at campaign startup, after
                # its waste routing is validated and the power supply is off.
                auto_home=False,
                syringe_volume=5000
            )

            print("AMF hardware initialized.")
            
        self.process_adapter_name = process_adapter or DEFAULT_PROCESS_ADAPTER
        self.adapter_config = dict(adapter_config or {})
        self.process_adapter = create_process_adapter(self.process_adapter_name, config=self.adapter_config)
        self.running_protocol_script = running_protocol_script or None
        self.running_protocol_module = None
        self.measurement_source_prefix = measurement_source_prefix
        self.measurement_source_signal = measurement_source_signal
        self.measurement_source_tag = measurement_source_tag
        settings = measurement_settings({"measurement_backend": measurement_backend, "uvvis_config": uvvis_config})
        if settings["measurement_backend"] not in ("opc", "uvvis"):
            raise ValueError("Measurement backend must be 'opc' or 'uvvis'.")
        self.measurement_backend = settings["measurement_backend"]
        self.uvvis_config = settings["uvvis_config"]
        self._uvvis_measurement = None
        self.last_measurement_result = None
        self._experiment_completed = False
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
        self.protocol_uvvis_dilution_fn = self._protocol_callable("uvvis_dilution_settings")
        self.protocol_uvvis_set_flow_fn = self._protocol_callable("set_uvvis_dilution_flow")
        self.experiment_status_placeholder = st.sidebar.empty()
        self.countdown_placeholder = st.empty()
        self.timer_placeholder = st.sidebar.empty()
        self.measurements_plot_placeholder = st.empty()
        self.start_time = None
        self.full_measurement_log = []  # Store all measurements for the full experiment
        self.tray_pos_waste = 0
        self.tray_pos_collect = 1
        self.volume_to_collect = volume_to_collect  # Volume to collect in mL

    @property
    def closed(self):
        return getattr(self, "_closed", False)

    def close(self):
        """Release USB/serial handles after the experiment's stop/cleanup path."""
        disconnect_devices(self)

    # ---------------------------------------------------------------------------
    #                          AMF HARDWARE HELPERS
    # ---------------------------------------------------------------------------

    def _write_amf_display(self, tag, value):
        """Display-only writes must not interrupt USB motion or shutdown."""
        if self.simulation_mode not in ("off", "hybrid"):
            return False
        try:
            if self.opc.write_value(tag, value, timeout=AMF_DISPLAY_TIMEOUT_S) is False:
                print(f"Warning: AMF display update failed for {tag}; USB control continues.")
                return False
        except Exception as exc:
            print(f"Warning: AMF display update failed for {tag}: {exc}; USB control continues.")
            return False
        return True

    def move_cell_valve(self, port: int):
        """
        Move the external AMF multiport valve and mirror the
        reported final position to LabVision through OPC.
        """
        if self.cell_valve is None:
            raise RuntimeError("AMF external multiport valve is not initialized.")

        final_position = self.cell_valve.move_to(int(port))

        self._write_amf_display(
            CELL_MULTIPORT_TAG,
            int(final_position),
        )

        return final_position

    def move_spm_valve(self, port: int):
        """
        Move the SPM+ internal valve and mirror the reported
        final position to LabVision through OPC.
        """
        if self.spm is None:
            raise RuntimeError("AMF SPM+ pump is not initialized.")

        final_position = self.spm.move_valve(int(port))

        self._write_amf_display(
            SPM_INTERNAL_VALVE_TAG,
            int(final_position),
        )

        return final_position

    def set_spm_flow(self, flow_uL_min: float):
        """
        Configure SPM speed in uL/min. LabVision shows this speed only during
        commanded motion; an idle syringe is displayed as zero flow.
        """
        if self.spm is None:
            raise RuntimeError("AMF SPM+ pump is not initialized.")

        flow_uL_min = float(flow_uL_min)

        actual_flow = self.spm.set_flow_rate(
            flow_uL_min
        )
        self._spm_flow_uL_min = actual_flow
        active = bool(getattr(self, "_spm_motion_active", False))
        if not active:
            self.spm.get_syringe_state()  # Confirm idle before displaying zero.
        self._write_amf_display(SPM_FLOW_TAG, actual_flow if active else 0.0)
        return actual_flow

    def home_spm_to_waste(self, waste_port):
        """Mirror the verified idle state and valve position after homing."""
        if self.spm is None:
            raise RuntimeError("AMF SPM+ pump is not initialized.")
        state = self.spm.home_to_waste(waste_port=waste_port)
        self._spm_motion_active = False
        self._spm_flow_uL_min = None  # Homing may reset the configured speed.
        self._write_amf_display(SPM_INTERNAL_VALVE_TAG, state["valve_position"])
        self._write_amf_display(SPM_FLOW_TAG, 0.0)
        return state

    def move_spm_to_volume(self, volume_uL, block=True):
        """Mirror syringe filling/dispensing without changing the USB sequence.

        Flow is the device's configured motion rate, not a flow-sensor reading.
        A failed command/uncertain stop must not falsely report zero flow.
        """
        if self.spm is None:
            raise RuntimeError("AMF SPM+ pump is not initialized.")
        flow = getattr(self, "_spm_flow_uL_min", None)
        if flow is None or not math.isfinite(flow) or flow <= 0:
            raise RuntimeError("Configure the SPM flow before moving the syringe.")

        def motion_started(moving):
            self._spm_motion_active = moving
            self._write_amf_display(SPM_FLOW_TAG, flow if moving else 0.0)

        state = self.spm.move_to_volume(volume_uL, block=block, on_started=motion_started)
        if block:
            self._spm_motion_active = False
            self._write_amf_display(SPM_FLOW_TAG, 0.0)
        return state

    def stop_spm(self):
        """
        Stop SPM+ motion immediately and mirror zero flow to LabVision.
        Remaining liquid stays inside the syringe.
        """
        if self.spm is None:
            return

        state = self.spm.hard_stop()
        self._spm_motion_active = False

        self._write_amf_display(
            SPM_FLOW_TAG,
            0.0,
        )
        return state

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
            self._degradation_inlet(parameters) if "Degradation" in objectives
            else float(parameters.get("substrate_concentration", 1.0)),
            flow_aq,
            flow_org,
            res_time,
            selected_objectives=objectives,
            directions=directions,
        )
        if "Degradation" in objectives:
            self._record_degradation_measurement(raw_area, parameters, len(self.full_measurement_log) - 1)
        return self._normalize_result_payload(result, objectives)

    # ---------------------------------------------------------------------------
    #                          RUNNING EXPERIMENT
    # ---------------------------------------------------------------------------
    @disconnect_on_error
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
        if self.closed:
            raise RuntimeError("This runner is disconnected. Start or resume the campaign to reconnect.")
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

        self.last_measurement_result = None
        self.last_degradation_result = None
        self._experiment_completed = False
        if objectives and "Degradation" in objectives:
            self._degradation_inlet(parameters)  # Reject an invalid denominator before preparation.
        if self.measurement_backend == "uvvis" and self.simulation_mode == "off":
            # Validate the folder/model before any protocol can start hardware.
            provider = self._get_uvvis_measurement()
            self._get_uvvis_dilution_settings(provider, parameters)
        hardware_started = False
        completed = False
        try:
            if self.simulation_mode in ["off", "hybrid"]:
                hardware_started = True
                if self.protocol_prepare_fn:
                    self.protocol_prepare_fn(self, parameters)
                else:
                    self.process_adapter.prepare_hardware(self, parameters)
            else:
                print("Full simulation mode enabled: skipping temperature and pump setup.")

            if self.simulation_mode in ["full"]:
                result = self.simulate_experiment(parameters, objectives, directions)
            else:
                measurement_log_start = len(self.full_measurement_log) if objectives and "Degradation" in objectives else 0
                mean_measurement = self.collect_measurements(iteration=experiment_number or 0, parameters=parameters)
                if objectives and "Degradation" in objectives:
                    self._record_degradation_measurement(mean_measurement, parameters, measurement_log_start)
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

            if self.use_autosampler and self.simulation_mode in ["off", "hybrid"]:
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
            completed = True
            return self._normalize_result_payload(result, objectives)
        finally:
            if hardware_started:
                # Protocol cleanup can avoid starting a cleaning pump after an
                # experiment failed, while its shutdown path still always runs.
                self._experiment_completed = completed
                try:
                    try:
                        if self.protocol_stop_pumps_fn:
                            self.protocol_stop_pumps_fn(self, parameters)
                    finally:
                        if self.protocol_cleanup_fn:
                            self.protocol_cleanup_fn(self, parameters)
                        else:
                            self.process_adapter.cleanup(self, parameters)
                except Exception as cleanup_error:
                    if completed:
                        raise
                    # Preserve the measurement/preparation error for the caller.
                    print(f"Hardware cleanup also failed: {cleanup_error}")

    # ---------------------------------------------------------------------------
    #                          SAVING FUNCTIONS
    # ---------------------------------------------------------------------------
    def save_full_measurements_to_csv(self, experiment_name):
        os.makedirs("raw_measurements", exist_ok=True)
        suffix = "_uvvis" if self.measurement_backend == "uvvis" and self.simulation_mode == "off" else ""
        filename = f"raw_measurements/{experiment_name.replace(' ', '_')}{suffix}_measurements.csv"
        if not self.full_measurement_log:
            return filename
        file_exists = os.path.isfile(filename) and os.path.getsize(filename) > 0
        old_keys = []
        if file_exists:
            with open(filename, newline="", encoding="utf-8-sig") as f:
                old_keys = next(csv.reader(f))
        keys = list(dict.fromkeys(old_keys + [key for row in self.full_measurement_log for key in row]))
        if file_exists and old_keys != keys:
            # Resume may add dilution columns to an older measurement log.
            # Expand its header atomically instead of appending misaligned rows.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                                 dir=os.path.dirname(filename), suffix=".tmp", delete=False) as f:
                    temporary = f.name
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    with open(filename, newline="", encoding="utf-8-sig") as previous:
                        for row in csv.DictReader(previous):
                            if None in row:
                                raise ValueError("Existing measurement CSV has inconsistent columns; log not overwritten.")
                            writer.writerow(row)
                    writer.writerows(self.full_measurement_log)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, filename)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            print(f"Full measurement log saved to {filename}")
            self.full_measurement_log.clear()
            return filename
        mode = "a" if file_exists else "w"
        with open(filename, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.full_measurement_log)
        print(f"Full measurement log saved to {filename}")
        self.full_measurement_log.clear()
        return filename


def replace_experiment_runner(previous_runner, *args, **kwargs):
    """Release the old runner before new device discovery attempts to open its ports."""
    close_runner(previous_runner)
    return ExperimentRunner(*args, **kwargs)
