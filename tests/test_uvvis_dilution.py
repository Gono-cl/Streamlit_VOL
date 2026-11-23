"""Automatic MB dilution with real CSV parsing and fully simulated pump I/O."""
import contextlib
import csv
import io
import json
import math
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from core.hardware.uvvis_measurement import UVVisMeasurement, MeasurementValidationError, measurement_settings
from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


PARAMS = {"current_density": 3.028, "applied_charge": 6.753,
          "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
          "cell": "EC3", "electrolyte": "NaCl"}


class FlowSpectra:
    """Continuously export distinct spectra reflecting the simulated water flow."""
    def __init__(self, folder, reaction_flow, concentration=200.0):
        self.folder = folder
        self.reaction_flow = reaction_flow
        self.concentration = concentration
        self.water_flow = 0.0
        self.now = 0.0
        self.next_export = 0.1
        self.index = 0
        self.delays = []
        self.events = []
        self.enabled = True
        self.signal = None

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds
        while self.next_export <= self.now:
            self.index += 1
            if self.enabled:
                value = self.concentration / (1 + self.water_flow / self.reaction_flow)
                if self.signal is not None:
                    value = self.signal(value, self.index)
                path = self.folder / f"my_acquired_spectra_{self.index:05d}.csv"
                path.write_text(f"Simulated flow spectrum\nWavelength [nm],Absorbance\n"
                                f"663,{value * .01:.12g}\n664,{value * .01:.12g}\n665,{value * .01:.12g}\n",
                                encoding="utf-8")
                timestamp = 1700000000.0 + self.next_export
                os.utime(path, (timestamp, timestamp))
            self.next_export += 0.5

    @contextlib.contextmanager
    def running(self):
        with patch("core.hardware.uvvis_dilution.time.sleep", side_effect=self.sleep), patch(
            "core.hardware.uvvis_dilution.time.monotonic", side_effect=lambda: self.now
        ), contextlib.redirect_stdout(io.StringIO()):
            yield


class AutoDilutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"dilution_test_{uuid.uuid4().hex}"
        self.root.mkdir()
        self.model = {"slope": .01, "intercept": 0.0, "r2": .999,
                      "range_min": 0.0, "range_max": 100.0, "wavelength": 664.0,
                      "concentration_unit": "mg/L"}
        self.model_path = self.root / "calibration.json"
        self.write_model()
        self.runner, self.sdk = make_runner(5000, initialized=False)
        self.runner.measurement_backend = "uvvis"
        self.runner.simulation_mode = "off"
        self.runner.uvvis_config = {"spectra_folder": str(self.root), "calibration_file": str(self.model_path),
                                    "concentration_unit": "mg/L", "n_replicates": 3,
                                    "replicate_interval": 0.0, "cv_threshold": 5.0,
                                    "max_extra_measurements": 1, "spectrum_timeout": 1.5}
        self.runner._uvvis_measurement = None
        self.runner.last_measurement_result = None
        self.runner.full_measurement_log = []
        self.runner.protocol_uvvis_dilution_fn = protocol.uvvis_dilution_settings
        self.runner.protocol_uvvis_set_flow_fn = protocol.set_uvvis_dilution_flow
        self.runner.protocol_uvvis_acquisition_metadata_fn = protocol.uvvis_acquisition_metadata
        self.runner.protocol_prepare_fn = Mock()
        self.runner.protocol_calculate_fn = Mock(side_effect=lambda runner, value, *args: {"Concentration": value})
        self.runner.protocol_stop_pumps_fn = Mock()
        self.runner.protocol_cleanup_fn = Mock()
        self.runner.use_autosampler = False
        self.runner._closed = False
        self.env = FlowSpectra(self.root, protocol._compute_operating_point(PARAMS)["total_flow_ml_min"])
        self.original_write = self.runner.opc.write_value.side_effect

        def write(tag, value, **kwargs):
            if tag == protocol.PUMP_ANALYTICAL_DILUTION_TAG:
                self.env.water_flow = value / 1000.0
                self.env.events.append((self.env.now, self.env.water_flow))
            return self.original_write(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write

    def tearDown(self):
        assert self.root.resolve().parent == Path(__file__).resolve().parent
        shutil.rmtree(self.root)

    def write_model(self):
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")

    def run_measurement(self, params=None):
        with self.env.running():
            return self.runner.run_experiment(params or PARAMS, objectives=["Concentration"])

    def test_in_range_direct_reading_never_starts_water_pump(self):
        self.env.concentration = 50.0
        result = self.run_measurement()
        self.assertAlmostEqual(result["Concentration"], 50)
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))
        self.assertEqual(self.env.delays.count(10.0), 1)
        fields = self.runner.measurement_result_fields()
        self.assertEqual(fields["UV/VIS Dilution Factor"], 1)
        self.assertEqual(fields["UV/VIS Dilution Status"], "direct")
        self.assertTrue(fields["UV/VIS Accepted"])

    def test_over_range_remeasures_and_returns_corrected_concentration(self):
        result = self.run_measurement()
        self.assertAlmostEqual(result["Concentration"], 200, places=6)
        fields = self.runner.measurement_result_fields()
        self.assertLessEqual(fields["UV/VIS Measured Concentration"], 100)
        self.assertAlmostEqual(fields["UV/VIS Corrected Concentration"], 200, places=6)
        flow = fields["UV/VIS Dilution Water Flow (mL/min)"]
        self.assertGreater(flow, 0)
        self.assertLessEqual(flow, 6.25)
        self.assertAlmostEqual(fields["UV/VIS Dilution Factor"], 1 + flow / self.env.reaction_flow)
        self.assertEqual(self.env.delays.count(10.0), 3)  # Direct, diluted, restored undiluted.
        self.assertEqual(self.env.water_flow, 0)
        self.assertTrue(fields["UV/VIS Dilution Stop Verified"])
        rows = self.runner.full_measurement_log
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["Spectrum File"] for row in rows}), 6)
        self.assertTrue(all(not row["Attempt Used for Result"] for row in rows[:3]))
        self.assertTrue(all(row["Attempt Used for Result"] for row in rows[3:]))
        self.assertTrue(all(row["Corrected Concentration"] is None for row in rows[:3]))

    def test_reaction_flow_is_recomputed_for_each_condition(self):
        params = {**PARAMS, "current_density": 4.0}
        self.env.reaction_flow = protocol._compute_operating_point(params)["total_flow_ml_min"]
        self.assertAlmostEqual(self.run_measurement(params)["Concentration"], 200, places=6)
        self.assertAlmostEqual(self.runner.measurement_result_fields()["UV/VIS Reaction Flow (mL/min)"],
                               self.env.reaction_flow)

    def test_excess_dilution_is_reduced_until_a_valid_reading_is_found(self):
        self.model["range_min"] = 40.0
        self.write_model()
        self.env.concentration = 101.0
        self.assertAlmostEqual(self.run_measurement()["Concentration"], 101, places=6)
        history = self.runner.last_measurement_result["dilution_history"]
        self.assertEqual([row["status"] for row in history], ["above_range", "below_range", "valid"])
        self.assertLess(history[2]["water_flow_ml_min"], history[1]["water_flow_ml_min"])

    def test_no_solution_at_water_limit_fails_without_objective_calculation(self):
        self.env.concentration = 2000
        with self.assertRaisesRegex(MeasurementValidationError, "Pump 3 limit"):
            self.run_measurement()
        self.runner.protocol_calculate_fn.assert_not_called()
        self.assertLessEqual(max(flow for _, flow in self.env.events), 6.25)
        self.assertEqual(self.env.water_flow, 0)
        self.assertFalse(any(row["Attempt Used for Result"] for row in self.runner.full_measurement_log))
        self.runner.protocol_stop_pumps_fn.assert_called_once()

    def test_retry_count_is_bounded(self):
        self.runner.adapter_config["uvvis_dilution_max_attempts"] = 1
        with self.assertRaisesRegex(MeasurementValidationError, "after 1 dilution attempts"):
            self.run_measurement()
        self.assertEqual(len(self.runner.full_measurement_log), 3)
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))

    def test_unstable_in_range_readings_do_not_trigger_dilution(self):
        self.env.concentration = 50
        self.env.signal = lambda value, index: value + (20 if index % 2 else -20)
        with self.assertRaisesRegex(MeasurementValidationError, "stability failed"):
            self.run_measurement()
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))
        self.runner.protocol_calculate_fn.assert_not_called()

    def test_direct_below_range_cannot_be_fixed_by_adding_water(self):
        self.model["range_min"] = 40
        self.write_model()
        self.env.concentration = 10
        with self.assertRaisesRegex(MeasurementValidationError, "outside the calibration range"):
            self.run_measurement()
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))

    def test_no_fresh_spectrum_times_out_and_restores_water_off(self):
        self.env.enabled = False
        with self.assertRaises(TimeoutError):
            self.run_measurement()
        self.assertEqual(self.env.water_flow, 0)
        self.runner.protocol_calculate_fn.assert_not_called()

    def test_wrong_units_fail_before_preparation_or_pump_commands(self):
        self.model["concentration_unit"] = "mM"
        self.write_model()
        self.runner.uvvis_config["concentration_unit"] = "mM"
        with self.assertRaisesRegex(ValueError, "requires.*mg/L"):
            self.run_measurement()
        self.runner.protocol_prepare_fn.assert_not_called()
        self.assertEqual(self.env.events, [])

    def test_invalid_limits_fail_before_preparation(self):
        for config in ({"uvvis_dilution_max_flow_ml_min": 7}, {"uvvis_dilution_max_flow_ml_min": True},
                       {"uvvis_dilution_wait_s": 0},
                       {"uvvis_dilution_max_attempts": True}):
            with self.subTest(config=config):
                self.runner.adapter_config = config
                self.runner._closed = False  # A preceding failed preflight disconnects the fake.
                with self.assertRaises(ValueError):
                    self.run_measurement()
                self.runner.protocol_prepare_fn.assert_not_called()
                self.assertEqual(self.env.events, [])

    def test_failed_water_start_is_not_accepted_and_off_is_still_attempted(self):
        original = self.runner.opc.write_value.side_effect
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: (
            False if tag == protocol.PUMP_ANALYTICAL_DILUTION_TAG and value > 0 else original(tag, value, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            self.run_measurement()
        self.assertEqual(self.env.water_flow, 0)
        self.runner.protocol_calculate_fn.assert_not_called()

    def test_water_readback_mismatch_aborts_and_still_attempts_stop(self):
        original = self.runner.opc.read_value.side_effect
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: (
            -1 if tag == protocol.PUMP_ANALYTICAL_DILUTION_TAG else original(tag, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "Could not verify analytical dilution flow"):
            self.run_measurement()
        self.assertEqual(self.env.events[-1][1], 0)
        self.runner.protocol_calculate_fn.assert_not_called()

    def test_failure_to_stop_after_valid_dilution_rejects_the_result(self):
        original = self.runner.opc.write_value.side_effect
        started = False

        def write(tag, value, **kwargs):
            nonlocal started
            if tag == protocol.PUMP_ANALYTICAL_DILUTION_TAG:
                if value > 0:
                    started = True
                elif started:
                    return False
            return original(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            self.run_measurement()
        self.runner.protocol_calculate_fn.assert_not_called()
        self.assertFalse(any(row["Attempt Used for Result"] for row in self.runner.full_measurement_log))
        self.assertFalse(self.runner.last_measurement_result["dilution_used_for_result"])

    def test_read_only_provider_never_calls_the_pump(self):
        self.env.concentration = 200
        provider = UVVisMeasurement(self.runner.uvvis_config)
        with self.env.running():
            result = provider.measure()
        self.assertEqual(result["status"], "above_range")
        self.assertEqual(self.env.events, [])

    def test_partial_acquisition_is_logged_and_pump_stopped_after_timeout(self):
        provider = self.runner._get_uvvis_measurement()

        def partial_measurement():
            provider.reader.get_latest_absorbance(provider.sample.wavelength)
            raise TimeoutError("Simulated export interruption")

        with patch.object(provider.sample, "measure_sample", side_effect=partial_measurement):
            with self.assertRaisesRegex(TimeoutError, "export interruption"):
                self.run_measurement()
        rows = self.runner.full_measurement_log
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Range Status"], "acquisition_failed")
        self.assertFalse(rows[0]["Accepted"])
        self.assertFalse(rows[0]["Attempt Used for Result"])
        self.assertIsNone(rows[0]["Corrected Concentration"])
        self.assertEqual(self.env.water_flow, 0)
        self.runner.protocol_calculate_fn.assert_not_called()

    def test_effective_limits_are_serialized_with_measurement_settings(self):
        self.env.concentration = 50
        self.run_measurement()
        config = json.loads(json.dumps(measurement_settings(self.runner.__dict__)))
        effective = config["uvvis_config"]["auto_dilution"]
        self.assertEqual(effective["max_water_flow_ml_min"], 3.33)
        self.assertEqual(effective["settle_time_s"], 10)
        self.assertEqual(effective["max_attempts"], 6)

    def test_hybrid_and_full_simulation_do_not_invoke_dilution_hooks(self):
        for mode in ("hybrid", "full"):
            with self.subTest(mode=mode):
                self.runner.simulation_mode = mode
                self.runner.protocol_uvvis_dilution_fn = Mock(side_effect=AssertionError("Unexpected dilution"))
                self.runner.protocol_uvvis_set_flow_fn = Mock(side_effect=AssertionError("Unexpected pump action"))
                self.runner.protocol_calculate_fn = lambda *args: {"Concentration": 1.0}
                self.run_measurement()
                self.runner.protocol_uvvis_dilution_fn.assert_not_called()
                self.runner.protocol_uvvis_set_flow_fn.assert_not_called()
        self.assertEqual(self.env.events, [])

    def test_real_mb_protocol_runs_dilution_and_then_water_cleaning(self):
        self.runner.protocol_prepare_fn = protocol.prepare_hardware
        self.runner.protocol_stop_pumps_fn = protocol.stop_pumps
        self.runner.protocol_cleanup_fn = protocol.cleanup
        with patch.object(protocol, "_log_operating_point"):
            self.assertAlmostEqual(self.run_measurement()["Concentration"], 200, places=6)
        self.assertEqual(self.env.water_flow, 0)
        self.assertTrue(self.runner._experiment_completed)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertEqual(self.sdk.events.count(("home", 1, False)), 1)

    def test_new_dilution_columns_expand_existing_raw_csv_without_losing_old_rows(self):
        self.env.concentration = 50
        self.run_measurement()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            folder = self.root / "raw_measurements"
            folder.mkdir()
            path = folder / "saved_uvvis_measurements.csv"
            path.write_text("Iteration,Value\n1,12.5\n", encoding="utf-8")
            self.runner.save_full_measurements_to_csv("saved")
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["Value"], "12.5")
            self.assertEqual(rows[0]["Dilution Factor"], "")
            self.assertEqual(rows[1]["Dilution Factor"], "1.0")
            self.assertTrue(all(None not in row for row in rows))
            self.assertEqual(self.runner.full_measurement_log, [])
        finally:
            os.chdir(previous)

    def test_csv_upgrade_failure_preserves_original_file_and_buffer(self):
        self.env.concentration = 50
        self.run_measurement()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            folder = self.root / "raw_measurements"
            folder.mkdir()
            path = folder / "saved_uvvis_measurements.csv"
            original = b"Iteration,Value\n1,12.5,unexpected_extra_column\n"
            path.write_bytes(original)
            buffered = list(self.runner.full_measurement_log)
            with self.assertRaisesRegex(ValueError, "inconsistent columns"):
                self.runner.save_full_measurements_to_csv("saved")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(self.runner.full_measurement_log, buffered)
            self.assertEqual(list(folder.glob("*.tmp")), [])
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
