"""Measurement integration tests using synthetic CSV exports and no hardware."""
import contextlib
import io
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from core.hardware.experimental_run import ExperimentRunner
from core.hardware.uvvis_measurement import (
    FreshSpectragryphReader,
    MeasurementValidationError,
    UVVisMeasurement,
    measurement_session_updates,
    measurement_settings,
)


class ExportClock:
    """Produce incrementing CSV exports while acquisition advances its clock."""

    def __init__(self, folder, values, spacing=0.6):
        self.folder = folder
        self.values = list(values)
        self.spacing = spacing
        self.now = 0.0
        self.index = 0

    def sleep(self, seconds):
        self.now += seconds
        while self.index < len(self.values) and self.now >= 0.1 + self.index * self.spacing:
            value = self.values[self.index]
            self.index += 1
            path = self.folder / f"my_acquired_spectra_{self.index:03d}.csv"
            path.write_text(f"Synthetic spectrum\nWavelength [nm],Absorbance\n663,{value}\n664,{value}\n665,{value}\n", encoding="utf-8")

    @contextlib.contextmanager
    def running(self):
        with patch("core.hardware.uvvis_measurement.time.monotonic", side_effect=lambda: self.now), patch(
            "core.hardware.uvvis_measurement.time.sleep", side_effect=self.sleep
        ), contextlib.redirect_stdout(io.StringIO()):
            yield


class UVVisIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"uvvis_test_{uuid.uuid4().hex}"
        self.root.mkdir()
        self.model = {
            "slope": 2.0, "intercept": 1.0, "r2": 0.999,
            "range_min": 0.0, "range_max": 1.0, "wavelength": 664.0,
            "concentration_unit": "mM",
        }
        self.model_path = self.root / "calibration.json"
        self.write_model()
        self.config = {
            "spectra_folder": str(self.root), "calibration_file": str(self.model_path),
            "concentration_unit": "mM", "n_replicates": 3, "replicate_interval": 0.0,
            "cv_threshold": 5.0, "max_extra_measurements": 2, "spectrum_timeout": 1.0,
        }

    def tearDown(self):
        assert self.root.resolve().parent == Path(__file__).resolve().parent
        shutil.rmtree(self.root)

    def write_model(self):
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")

    def runner(self, mode="off", use_autosampler=False):
        opc = Mock()
        opc.read_value.side_effect = AssertionError("Unexpected hardware read")
        opc.write_value.side_effect = AssertionError("Unexpected hardware write")
        with patch("core.hardware.experimental_run.st.empty"), patch("core.hardware.experimental_run.st.sidebar.empty"):
            runner = ExperimentRunner(opc, "", simulation_mode=mode, measurement_backend="uvvis", uvvis_config=self.config, use_autosampler=use_autosampler)
        runner.protocol_prepare_fn = Mock()
        runner.protocol_stop_pumps_fn = Mock()
        runner.protocol_cleanup_fn = Mock()
        return runner

    def test_valid_concentration_and_yield_reach_runner_with_provenance(self):
        runner = self.runner()
        clock = ExportClock(self.root, [1.4, 1.401, 1.399])
        with clock.running():
            result = runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration", "Yield"])
        self.assertAlmostEqual(result["Concentration"], 0.2)
        self.assertAlmostEqual(result["Yield"], 40.0)
        self.assertEqual(len(runner.full_measurement_log), 3)
        self.assertEqual(len({row["Spectrum File"] for row in runner.full_measurement_log}), 3)
        self.assertTrue(all(row["Accepted"] for row in runner.full_measurement_log))
        self.assertEqual(runner.measurement_result_fields()["Concentration Unit"], "mM")
        self.assertEqual(len(runner.measurement_result_fields()["UV/VIS Calibration ID"]), 64)
        runner.protocol_cleanup_fn.assert_called_once()
        runner.opc.read_value.assert_not_called()
        runner.opc.write_value.assert_not_called()

    def test_existing_spectrum_is_not_reused_and_timeout_runs_cleanup(self):
        (self.root / "my_acquired_spectra_old.csv").write_text("Synthetic\nWavelength,Absorbance\n664,1.4\n")
        runner = self.runner()
        runner.protocol_calculate_fn = Mock()
        with ExportClock(self.root, []).running(), self.assertRaises(TimeoutError):
            runner.run_experiment({"substrate_concentration": 1.0}, objectives=["Concentration"])
        runner.protocol_calculate_fn.assert_not_called()
        runner.protocol_cleanup_fn.assert_called_once()
        self.assertEqual(runner.full_measurement_log, [])

    def test_one_new_spectrum_cannot_supply_multiple_replicates(self):
        provider = UVVisMeasurement(self.config)
        with ExportClock(self.root, [1.4]).running(), self.assertRaises(TimeoutError):
            provider.measure()
        self.assertEqual(len(provider.reader.acquisitions), 1)

    def test_unstable_results_are_bounded_logged_and_not_calculated(self):
        runner = self.runner()
        runner.protocol_calculate_fn = Mock()
        with ExportClock(self.root, [1.1, 2.9, 1.1, 2.9, 1.1]).running(), self.assertRaises(MeasurementValidationError):
            runner.run_experiment({}, objectives=["Concentration"])
        self.assertEqual(len(runner.full_measurement_log), 5)
        self.assertFalse(any(row["Accepted"] for row in runner.full_measurement_log))
        self.assertFalse(runner.last_measurement_result["accepted"])
        runner.protocol_calculate_fn.assert_not_called()
        runner.protocol_cleanup_fn.assert_called_once()

    def test_rolling_window_recovers_after_transient(self):
        provider = UVVisMeasurement(self.config)
        with ExportClock(self.root, [2.9, 1.4, 1.4, 1.4]).running():
            result = provider.measure()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["extra_measurements"], 1)
        self.assertAlmostEqual(provider.require_valid(result), 0.2)

    def test_out_of_range_does_not_reach_objectives(self):
        runner = self.runner()
        runner.protocol_calculate_fn = Mock()
        with ExportClock(self.root, [4.0] * 3).running(), self.assertRaises(MeasurementValidationError):
            runner.run_experiment({}, objectives=["Concentration"])
        self.assertEqual(runner.last_measurement_result["status"], "above_range")
        runner.protocol_calculate_fn.assert_not_called()
        runner.protocol_cleanup_fn.assert_called_once()

    def test_invalid_model_is_rejected_before_hardware_preparation(self):
        for key, value in (("slope", 0.0), ("intercept", float("nan")), ("range_max", -1.0), ("wavelength", -2.0)):
            with self.subTest(key=key):
                original = self.model[key]
                self.model[key] = value
                self.write_model()
                runner = self.runner()
                with self.assertRaises(ValueError):
                    runner.run_experiment({}, objectives=["Concentration"])
                runner.protocol_prepare_fn.assert_not_called()
                self.model[key] = original

    def test_units_are_explicit_and_legacy_models_work(self):
        self.config["concentration_unit"] = "mg/L"
        with self.assertRaisesRegex(ValueError, "Calibration unit"):
            UVVisMeasurement(self.config)
        del self.model["concentration_unit"]
        self.write_model()
        self.assertEqual(UVVisMeasurement(self.config).concentration_unit, "mg/L")

    def test_nonfinite_spectrum_and_missing_wavelength_are_rejected(self):
        for wavelength, absorbance in ((664, "nan"), (400, "1.4")):
            with self.subTest(wavelength=wavelength):
                reader = FreshSpectragryphReader(self.root, "bad_*.csv", 0.5)
                reader.begin_measurement()
                path = self.root / f"bad_{wavelength}.csv"
                path.write_text(f"Synthetic\nWavelength,Absorbance\n{wavelength},{absorbance}\n")
                with ExportClock(self.root, []).running(), self.assertRaises(TimeoutError):
                    reader.get_latest_absorbance(664)

    def test_zero_mean_with_nonzero_noise_fails_cv(self):
        provider = UVVisMeasurement(self.config)
        mean, sd, cv = provider.sample.calculate_statistics([-0.01, 0.01])
        self.assertEqual(mean, 0)
        self.assertGreater(sd, 0)
        self.assertEqual(cv, float("inf"))

    def test_simulation_avoids_spectra_and_full_simulation_avoids_autosampler(self):
        self.config["calibration_file"] = "does-not-exist.json"
        for mode in ("hybrid", "full"):
            with self.subTest(mode=mode):
                runner = self.runner(mode=mode, use_autosampler=mode == "full")
                runner.autosampler = Mock()
                with contextlib.redirect_stdout(io.StringIO()):
                    result = runner.run_experiment({"substrate_concentration": 1.0}, objectives=["Concentration"])
                self.assertIn("Concentration", result)
                self.assertIsNone(runner._uvvis_measurement)
                runner.opc.read_value.assert_not_called()
                runner.opc.write_value.assert_not_called()
                runner.autosampler.clean_before_collect.assert_not_called()

    def test_settings_round_trip_and_old_runs_default_to_opc(self):
        saved = measurement_settings({"measurement_backend": "uvvis", "uvvis_config": self.config})
        restored = measurement_session_updates(json.loads(json.dumps(saved)))
        self.assertEqual(restored["uvvis_config"], saved["uvvis_config"])
        self.assertEqual(restored["uvvis_calibration_file"], str(self.model_path))
        self.assertEqual(measurement_settings({})["measurement_backend"], "opc")

    def test_resume_rejects_changed_calibration_before_preparation(self):
        runner = self.runner()
        runner._get_uvvis_measurement()
        self.config = measurement_settings(runner.__dict__)["uvvis_config"]
        self.model["slope"] = 3.0
        self.write_model()
        resumed = self.runner()
        with self.assertRaisesRegex(ValueError, "calibration has changed"):
            resumed.run_experiment({}, objectives=["Concentration"])
        resumed.protocol_prepare_fn.assert_not_called()

    def test_exports_faster_than_polling_do_not_prevent_acquisition(self):
        provider = UVVisMeasurement(self.config)
        with ExportClock(self.root, [1.4] * 20, spacing=0.1).running():
            result = provider.measure()
        self.assertAlmostEqual(provider.require_valid(result), 0.2)
        self.assertEqual(len(result["acquisitions"]), 3)

    def test_streamlit_pages_offer_uvvis_and_display_a_test_result(self):
        from streamlit.testing.v1 import AppTest

        provider = UVVisMeasurement(self.config)
        with ExportClock(self.root, [1.4] * 3).running():
            result = provider.measure()
        for filename in ("Single_Objective.py", "Multi_Objective.py", "DoE_Executor.py"):
            with self.subTest(page=filename), patch("requests.get", side_effect=AssertionError("Unexpected network request")):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / filename), default_timeout=30).run()
                self.assertFalse(app.exception)
                app.selectbox(key="measurement_backend").set_value("uvvis").run()
                self.assertFalse(app.exception)
                self.assertEqual(app.text_input(key="uvvis_concentration_unit").value, "mM")
                with patch("core.hardware.measurement_ui.UVVisMeasurement") as provider_class:
                    provider_class.return_value.measure.return_value = result
                    app.button(key="uvvis_test_measurement").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any("Validated measurement" in message.value for message in app.success))
                self.assertTrue(any("mM" in item.value for item in app.metric))

    def test_opc_retry_limit_is_enforced(self):
        runner = self.runner()
        runner.measurement_backend = "opc"
        runner._read_measurement = Mock(side_effect=[20, 100, 20, 100])
        with patch("core.hardware.mixins.measurement_mixin.time.sleep"), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(MeasurementValidationError):
            runner.collect_measurements(max_measurements=4)
        self.assertEqual(runner._read_measurement.call_count, 4)


if __name__ == "__main__":
    unittest.main()
