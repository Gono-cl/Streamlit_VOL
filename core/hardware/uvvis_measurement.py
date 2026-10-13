"""Saved-calibration UV/VIS measurements; this module never controls hardware."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from InlineAnalytics.SampleMeasurement.sample_measurement import SampleMeasurement
from InlineAnalytics.Spectrometer.spectragryph_reader import SpectragryphReader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UVVIS_DEFAULTS = {
    "spectra_folder": "",
    "file_pattern": "my_acquired_spectra_*.csv",
    "calibration_file": "InlineAnalytics/calibration_model.json",
    "concentration_unit": "mM",
    "n_replicates": 5,
    "replicate_interval": 2.0,
    "cv_threshold": 5.0,
    "max_extra_measurements": 10,
    "spectrum_timeout": 30.0,
}


class MeasurementValidationError(RuntimeError):
    """A measurement must not be passed to objective calculation or optimization."""


def measurement_settings(settings) -> dict:
    """Serializable runner settings, including backward-compatible OPC defaults."""
    config = {**UVVIS_DEFAULTS, **dict(settings.get("uvvis_config", {}) or {})}
    return {
        "measurement_backend": settings.get("measurement_backend", "opc"),
        "uvvis_config": config,
    }


def measurement_session_updates(settings) -> dict:
    restored = measurement_settings(settings)
    return {
        **restored,
        **{f"uvvis_{key}": value for key, value in restored["uvvis_config"].items()},
        "uvvis_last_test": None,
    }


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


class FreshSpectragryphReader(SpectragryphReader):
    """Wait for distinct, complete CSV exports produced after measurement begins."""

    def __init__(self, folder, file_pattern, timeout, poll_interval=0.2):
        super().__init__(folder, file_pattern)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.acquisitions = []
        self._seen = set()

    def begin_measurement(self):
        self._seen = set(self.folder.glob(self.file_pattern))
        self.acquisitions = []

    def get_latest_absorbance(self, target_wavelength=664.0):
        deadline = time.monotonic() + self.timeout
        observed = {}
        last_error = None
        while time.monotonic() < deadline:
            paths = list(self.folder.glob(self.file_pattern))
            candidates = []
            for path in paths:
                if path in self._seen or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                    if stat.st_size:
                        candidates.append((stat.st_mtime_ns, path, stat.st_size))
                except OSError:
                    continue
            if candidates:
                stable = [item for item in candidates if observed.get(item[1]) == (item[0], item[2])]
                observed = {path: (modified, size) for modified, path, size in candidates}
                if stable:
                    modified, path, size = max(stable, key=lambda item: (item[0], item[1].name))
                    try:
                        wavelength, absorbance = self.read_spectrum(path)
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (modified, size):
                            observed.pop(path, None)
                            continue
                        if not len(wavelength) or not np.all(np.isfinite(wavelength)) or not np.all(np.isfinite(absorbance)):
                            raise ValueError("Spectrum contains empty or non-finite data.")
                        if not float(wavelength.min()) <= target_wavelength <= float(wavelength.max()):
                            raise ValueError(f"Spectrum does not cover {target_wavelength:g} nm.")
                        index = int(np.argmin(np.abs(wavelength - target_wavelength)))
                        measured_wavelength = float(wavelength[index])
                        value = float(absorbance[index])
                    except (OSError, ValueError, IndexError) as exc:
                        last_error = exc
                    else:
                        # Skip older exports from this poll as well as the selected file.
                        self._seen.update(candidate_path for candidate_time, candidate_path, _ in candidates if candidate_time <= modified)
                        self.acquisitions.append({
                            "file": str(path),
                            "timestamp": datetime.fromtimestamp(modified / 1e9, timezone.utc).isoformat(),
                            "wavelength": measured_wavelength,
                            "absorbance": value,
                        })
                        return path, measured_wavelength, value
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
        detail = f" Last export error: {last_error}" if last_error else ""
        raise TimeoutError(
            f"No new complete UV/VIS spectrum within {self.timeout:g} seconds. "
            "Check continuous CSV export and automatic filename incrementing in Spectragryph."
            + detail
        )


class UVVisMeasurement:
    """Reuse inline analytics replicate/CV validation with fresh-file acquisition."""

    def __init__(self, config: dict):
        self.config = {**UVVIS_DEFAULTS, **dict(config or {})}
        self._validate_settings()
        self.calibration_file = resolve_path(self.config["calibration_file"])
        self.reader = FreshSpectragryphReader(
            resolve_path(self.config["spectra_folder"]),
            self.config["file_pattern"],
            float(self.config["spectrum_timeout"]),
        )
        if not self.reader.folder.is_dir():
            raise ValueError(f"UV/VIS spectra folder does not exist: {self.reader.folder}")
        self.sample = SampleMeasurement(
            self.reader,
            calibration_file=self.calibration_file,
            n_replicates=int(self.config["n_replicates"]),
            replicate_interval=float(self.config["replicate_interval"]),
            cv_threshold=float(self.config["cv_threshold"]),
            max_extra_measurements=int(self.config["max_extra_measurements"]),
            results_file=None,
        )
        self._validate_model()
        model_unit = str(self.sample.calibration_model.get("concentration_unit", "")).strip()
        self.concentration_unit = str(self.config["concentration_unit"]).strip()
        if model_unit and model_unit != self.concentration_unit:
            raise ValueError(
                f"Calibration unit is {model_unit}, but the selected unit is {self.concentration_unit}. "
                "Select the calibration's unit; changing a label does not convert its coefficients."
            )
        self.calibration_id = hashlib.sha256(
            json.dumps(self.sample.calibration_model, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        expected_id = self.config.get("expected_calibration_id")
        if expected_id and expected_id != self.calibration_id:
            raise ValueError("The saved calibration has changed since this run. Restore that calibration or start a new run.")
        self.last_result = None

    def _validate_settings(self):
        for key in ("spectra_folder", "file_pattern", "calibration_file", "concentration_unit"):
            if not str(self.config[key]).strip():
                raise ValueError(f"UV/VIS setting '{key}' is required.")
        pattern = str(self.config["file_pattern"])
        if "/" in pattern or "\\" in pattern or ".." in pattern:
            raise ValueError("Spectrum filename pattern must refer to files in the selected folder.")
        for key, minimum in (("n_replicates", 2), ("max_extra_measurements", 0)):
            value = float(self.config[key])
            if not math.isfinite(value) or not value.is_integer() or value < minimum:
                raise ValueError(f"UV/VIS '{key}' must be an integer >= {minimum}.")
        for key in ("replicate_interval", "cv_threshold", "spectrum_timeout"):
            value = float(self.config[key])
            if not math.isfinite(value) or value < 0 or (key == "spectrum_timeout" and value == 0):
                raise ValueError(f"Invalid UV/VIS '{key}'.")

    def _validate_model(self):
        for key in ("slope", "intercept", "r2", "range_min", "range_max", "wavelength"):
            value = self.sample.calibration_model.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Calibration '{key}' must be a finite number.")
        sample = self.sample
        if sample.slope == 0:
            raise ValueError("Calibration slope must be nonzero.")
        if sample.range_min >= sample.range_max:
            raise ValueError("Calibration range_min must be less than range_max.")
        if sample.wavelength <= 0 or not 0 <= sample.r2 <= 1:
            raise ValueError("Calibration wavelength or R² is invalid.")

    def measure(self) -> dict:
        self.last_result = None
        self.reader.begin_measurement()
        result = self.sample.measure_sample()
        result.update({
            "source": "uvvis",
            "concentration_unit": self.concentration_unit,
            "calibration_file": str(self.calibration_file),
            "calibration_id": self.calibration_id,
            "calibration_range_min": self.sample.range_min,
            "calibration_range_max": self.sample.range_max,
            "acquisitions": list(self.reader.acquisitions),
        })
        self.last_result = result
        return result

    def require_valid(self, result: dict) -> float:
        if not result["accepted"]:
            raise MeasurementValidationError(
                f"UV/VIS stability failed: CV {result['absorbance_cv']:.2f}% "
                f"exceeds {float(self.config['cv_threshold']):g}% after "
                f"{result['extra_measurements']} extra measurements."
            )
        concentration = float(result["concentration"])
        if not math.isfinite(concentration) or result["status"] != "valid":
            raise MeasurementValidationError(
                f"UV/VIS concentration {concentration:g} {self.concentration_unit} "
                f"is outside the calibration range "
                f"{self.sample.range_min:g}–{self.sample.range_max:g} {self.concentration_unit}."
            )
        return concentration
