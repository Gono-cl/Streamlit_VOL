"""Measurement and simulation helpers for ExperimentRunner."""
from __future__ import annotations

import time
import json
from datetime import datetime
from typing import List

import numpy as np

from core.hardware.uvvis_measurement import UVVisMeasurement, MeasurementValidationError


class MeasurementMixin:
    def _get_uvvis_measurement(self):
        if getattr(self, "_uvvis_measurement", None) is None:
            self._uvvis_measurement = UVVisMeasurement(self.uvvis_config)
            self.uvvis_config["expected_calibration_id"] = self._uvvis_measurement.calibration_id
        return self._uvvis_measurement

    def measurement_result_fields(self):
        result = getattr(self, "last_measurement_result", None)
        if not result:
            return {}
        return {
            "Measurement Source": "UV/VIS",
            "Concentration Unit": result["concentration_unit"],
            "UV/VIS Concentration": result["concentration"],
            "UV/VIS Absorbance": result["absorbance"],
            "UV/VIS Absorbance SD": result["absorbance_sd"],
            "UV/VIS CV (%)": result["absorbance_cv"],
            "UV/VIS Accepted": result["accepted"],
            "UV/VIS Range Status": result["status"],
            "UV/VIS Extra Measurements": result["extra_measurements"],
            "UV/VIS Calibration": result["calibration_file"],
            "UV/VIS Calibration ID": result["calibration_id"],
        }

    def _collect_uvvis_measurements(self, iteration, parameters):
        provider = self._get_uvvis_measurement()
        result = None
        try:
            result = provider.measure()
            self.last_measurement_result = result
            return provider.require_valid(result)
        finally:
            for index, acquisition in enumerate(provider.reader.acquisitions, 1):
                self.full_measurement_log.append({
                    "Iteration": iteration,
                    "Timestamp": acquisition["timestamp"],
                    **(parameters or {}),
                    "Measurement #": index,
                    "Value": float(provider.sample.calculate_concentration(acquisition["absorbance"])),
                    "Absorbance": acquisition["absorbance"],
                    "Wavelength (nm)": acquisition["wavelength"],
                    "Spectrum File": acquisition["file"],
                    "Concentration Unit": provider.concentration_unit,
                    "Accepted": bool(result and result["accepted"] and result["status"] == "valid"),
                    "Range Status": result["status"] if result else "acquisition_failed",
                    "Absorbance CV (%)": result["absorbance_cv"] if result else "",
                    "Calibration ID": provider.calibration_id,
                    "Calibration Model": json.dumps(provider.sample.calibration_model, sort_keys=True),
                })

    def calculate_rsd(self, measurements: List[float]):
        return (np.std(measurements) / np.mean(measurements)) * 100 if np.mean(measurements) != 0 else float("inf")

    def _resolve_measurement_tag(self, parameters=None):
        configured_tag = str(getattr(self, "measurement_source_tag", "") or "").strip()
        if configured_tag:
            return configured_tag

        protocol_tag_fn = getattr(self, "protocol_measurement_tag_fn", None)
        if callable(protocol_tag_fn):
            custom_tag = protocol_tag_fn(self, parameters)
            custom_tag = str(custom_tag or "").strip()
            if custom_tag:
                return custom_tag

        prefix = str(getattr(self, "measurement_source_prefix", "") or "").strip()
        signal = str(getattr(self, "measurement_source_signal", "") or "").strip()
        if not prefix:
            prefix = "OpusOPCSvr.HP-CZC3484P17->"
        if not signal:
            signal = "PDA - mM"
        return f"{prefix}{signal}"

    def _read_measurement(self, parameters=None):
        if self.simulation_mode in ["full", "hybrid"]:
            return self.synthetic_raw_area((parameters or {}).get("residence_time", 20))
        measurement_tag = self._resolve_measurement_tag(parameters)
        product_area = float(self.opc.read_value(measurement_tag))
        return product_area

    def collect_measurements(self, rsd_threshold=3, max_measurements=15, iteration=0, parameters=None, min_mean_threshold=10.0, abs_std_threshold=0.5):
        """
        Collect measurements with quality control based on RSD or absolute std dev.

        Args:
            rsd_threshold: RSD threshold for high-value measurements (%)
            max_measurements: Maximum number of measurements to take
            iteration: Current iteration number
            parameters: Experiment parameters
            min_mean_threshold: Below this mean, use absolute std dev instead of RSD
            abs_std_threshold: Absolute std dev threshold for low-value measurements
        """
        if getattr(self, "measurement_backend", "opc") == "uvvis" and self.simulation_mode == "off":
            return self._collect_uvvis_measurements(iteration, parameters)

        measurements = []
        all_measurements = []

        while len(measurements) < 3:
            val = self._read_measurement(parameters=parameters)
            print(f"Measurement {len(measurements)+1} = {val:.2f}")
            measurements.append(val)
            all_measurements.append(val)
            if len(measurements) < 3 and self.simulation_mode == "off":
                time.sleep(20)

        mean_val = np.mean(measurements)
        rsd = self.calculate_rsd(measurements)
        abs_std = np.std(measurements)

        # Determine which metric to use based on mean value
        if mean_val < min_mean_threshold:
            print(f"\nInitial Abs StdDev = {abs_std:.3f} (mean too low for RSD: {mean_val:.2f})")
            use_rsd = False
            is_acceptable = abs_std < abs_std_threshold
        else:
            print(f"\nInitial RSD = {rsd:.2f}%")
            use_rsd = True
            is_acceptable = rsd < rsd_threshold

        while not is_acceptable and len(all_measurements) < max_measurements:
            if use_rsd:
                print(f"Warning: RSD too high ({rsd:.2f}% > {rsd_threshold}%). Taking another measurement...")
            else:
                print(f"Warning: Abs StdDev too high ({abs_std:.3f} > {abs_std_threshold}). Taking another measurement...")

            if self.simulation_mode == "off":
                time.sleep(20)  # Wait before next measurement
            new_val = self._read_measurement(parameters=parameters)
            print(f"New Measurement = {new_val:.2f}")
            measurements = measurements[-2:] + [new_val]
            all_measurements.append(new_val)

            mean_val = np.mean(measurements)
            rsd = self.calculate_rsd(measurements)
            abs_std = np.std(measurements)

            # Re-evaluate which metric to use
            if mean_val < min_mean_threshold:
                use_rsd = False
                is_acceptable = abs_std < abs_std_threshold
                print(f"Updated Abs StdDev = {abs_std:.3f}")
            else:
                use_rsd = True
                is_acceptable = rsd < rsd_threshold
                print(f"Updated RSD = {rsd:.2f}%")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for idx, val in enumerate(all_measurements, 1):
            self.full_measurement_log.append({
                "Iteration": iteration,
                "Timestamp": timestamp,
                **(parameters or {}),
                "Measurement #": idx,
                "Value": val
            })

        if not is_acceptable:
            raise MeasurementValidationError(f"OPC measurement failed stability after {len(all_measurements)} readings.")
        return float(np.mean(measurements))

    def synthetic_raw_area(self, res_time):
        """
        Generate synthetic raw area based on residence time and ratio_org_aq.
        Shorter residence time and lower ratio yield higher area.
        Output constrained between 3.0 and 4.0.
        """
        base = 4.0 - 0.015 * res_time + 0.3 * (1.5)
        noise = np.random.normal(0, 0.05)
        return float(np.clip(base + noise, 3.0, 4.0))
