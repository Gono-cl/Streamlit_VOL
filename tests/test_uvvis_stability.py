"""Exercise low-signal stability decisions with fresh synthetic spectra."""

import csv
import json
import math
import unittest
from unittest.mock import patch

from core.hardware.uvvis_measurement import UVVisMeasurement, MeasurementValidationError, measurement_settings, measurement_session_updates
from tests.test_uvvis_integration import ExportClock
from tests import test_uvvis_integration as integration_helpers
from tests import test_uvvis_dilution as dilution_helpers


class UVVisStabilityTests(unittest.TestCase):
    setUp = integration_helpers.UVVisIntegrationTests.setUp
    tearDown = integration_helpers.UVVisIntegrationTests.tearDown
    write_model = integration_helpers.UVVisIntegrationTests.write_model
    runner = integration_helpers.UVVisIntegrationTests.runner

    def low_signal_model(self, mode="auto", sd_limit=0.002):
        self.model.update(slope=0.01, intercept=0.0, range_max=50.0, concentration_unit="mg/L")
        self.write_model()
        self.config.update(concentration_unit="mg/L", stability_mode=mode, sd_threshold=sd_limit)

    def measure(self, values):
        provider = UVVisMeasurement(self.config)
        with ExportClock(self.root, values).running():
            result = provider.measure()
        return provider, result

    def test_automatic_accepts_low_signal_by_sd_despite_high_cv(self):
        self.low_signal_model()
        provider, result = self.measure([0.004, 0.005, 0.006])
        self.assertGreater(result["absorbance_cv"], 5)
        self.assertEqual(result["stability_criterion"], "sd")
        self.assertEqual(result["stability_units"], "AU")
        self.assertAlmostEqual(provider.require_valid(result), 0.5)
        self.assertEqual(result["extra_measurements"], 0)

    def test_old_cv_mode_still_rejects_the_same_low_signal(self):
        self.low_signal_model("cv", None)
        provider, result = self.measure([0.004, 0.005, 0.006, 0.004, 0.006])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["extra_measurements"], 2)
        with self.assertRaisesRegex(MeasurementValidationError, "CV"):
            provider.require_valid(result)

    def test_automatic_uses_cv_at_higher_absorbance(self):
        self.low_signal_model()
        provider, result = self.measure([0.4, 0.42, 0.44])
        self.assertGreater(result["absorbance_sd"], 0.002)
        self.assertEqual(result["stability_criterion"], "cv")
        self.assertAlmostEqual(provider.require_valid(result), 42)

    def test_sd_only_rejects_high_absolute_variation_even_if_cv_passes(self):
        self.low_signal_model("sd")
        provider, result = self.measure([0.4, 0.42, 0.44, 0.4, 0.44])
        self.assertFalse(result["accepted"])
        with self.assertRaisesRegex(MeasurementValidationError, "SD .* AU"):
            provider.require_valid(result)

    def test_low_signal_with_excess_sd_is_bounded_and_rejected(self):
        self.low_signal_model()
        provider, result = self.measure([0.0, 0.01, 0.02, 0.0, 0.02])
        self.assertFalse(result["accepted"])
        self.assertEqual(len(result["acquisitions"]), 5)
        with self.assertRaises(MeasurementValidationError):
            provider.require_valid(result)

    def test_zero_mean_with_finite_noise_can_pass_sd(self):
        self.low_signal_model()
        provider, result = self.measure([-0.001, 0.0, 0.001])
        self.assertTrue(math.isinf(result["absorbance_cv"]))
        self.assertEqual(result["stability_criterion"], "sd")
        self.assertEqual(provider.require_valid(result), 0.0)

    def test_crossover_and_nonfinite_statistics(self):
        self.low_signal_model()
        sample = UVVisMeasurement(self.config).sample
        at_limit = sample.stability_decision(0.04, 0.002, 5.0)
        self.assertTrue(at_limit["accepted"])
        self.assertEqual(at_limit["stability_criterion"], "sd")
        for mean, sd, cv in [(math.nan, 0.001, 0), (0.0, math.inf, math.inf)]:
            self.assertFalse(sample.stability_decision(mean, sd, cv)["accepted"])

    def test_sd_mode_requires_a_valid_tolerance_before_hardware(self):
        self.low_signal_model()
        for mode in ("sd", "auto"):
            for value in (None, 0, -1, math.nan, math.inf, True):
                with self.subTest(mode=mode, value=value):
                    self.config.update(stability_mode=mode, sd_threshold=value)
                    runner = self.runner()
                    with self.assertRaises(ValueError):
                        runner.run_experiment({}, objectives=["Concentration"])
                    runner.protocol_prepare_fn.assert_not_called()

    def test_sd_acceptance_keeps_calibration_range_validation(self):
        self.low_signal_model()
        self.model["range_min"] = 1.0
        self.write_model()
        provider, result = self.measure([0.004, 0.005, 0.006])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "below_range")
        with self.assertRaisesRegex(MeasurementValidationError, "outside the calibration range"):
            provider.require_valid(result)

    def test_runner_saves_sd_policy_and_final_window_provenance(self):
        self.low_signal_model()
        runner = self.runner()
        with ExportClock(self.root, [0.2, 0.005, 0.005, 0.005]).running():
            result = runner.run_experiment({"substrate_concentration": 1.0}, objectives=["Concentration"])
        self.assertAlmostEqual(result["Concentration"], 0.5)
        rows = runner.full_measurement_log
        self.assertEqual([r["Used for Stability Window"] for r in rows], [False, True, True, True])
        self.assertTrue(all(r["Stability Criterion"] == "sd" for r in rows))
        self.assertTrue(all(r["SD Limit (AU)"] == 0.002 for r in rows))
        self.assertTrue(all(r["Absorbance SD (AU)"] == 0 for r in rows))
        fields = runner.measurement_result_fields()
        self.assertEqual(fields["UV/VIS Stability Mode"], "auto")
        self.assertEqual(fields["UV/VIS Stability Units"], "AU")
        self.assertEqual(fields["UV/VIS Stability Limit"], 0.002)

    def test_settings_round_trip_and_legacy_default(self):
        self.low_signal_model()
        saved = measurement_settings({"measurement_backend": "uvvis", "uvvis_config": self.config})
        restored = measurement_session_updates(json.loads(json.dumps(saved)))
        self.assertEqual(restored["uvvis_stability_mode"], "auto")
        self.assertEqual(restored["uvvis_sd_threshold"], 0.002)
        self.assertEqual(measurement_settings({})["uvvis_config"]["stability_mode"], "cv")
        self.assertIsNone(measurement_settings({})["uvvis_config"]["sd_threshold"])

    def test_legacy_sample_csv_expands_without_misaligning_rows(self):
        self.low_signal_model()
        path = self.root / "sample_results.csv"
        path.write_text("timestamp,concentration\nold,12.0\n", encoding="utf-8")
        provider = UVVisMeasurement(self.config)
        provider.sample.results_file = path
        with ExportClock(self.root, [0.005] * 6).running():
            provider.measure()
            provider.measure()
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["timestamp"], "old")
        self.assertEqual(rows[0]["concentration"], "12.0")
        self.assertTrue(all(None not in r and None not in r.values() for r in rows))
        self.assertEqual(rows[-1]["stability_criterion"], "sd")

    def test_ui_sd_limit_starts_empty_and_policy_is_configurable(self):
        from streamlit.testing.v1 import AppTest
        script = """import streamlit as st
from core.hardware.measurement_ui import render_measurement_settings
st.session_state.setdefault('measurement_backend', 'uvvis')
render_measurement_settings()
"""
        app = AppTest.from_string(script).run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.number_input(key="uvvis_sd_threshold").value)
        app.selectbox(key="uvvis_stability_mode").set_value("auto").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("positive SD limit" in w.value for w in app.warning))
        app.number_input(key="uvvis_sd_threshold").set_value(0.002).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["uvvis_config"]["sd_threshold"], 0.002)
        app.selectbox(key="uvvis_stability_mode").set_value("cv").run()
        app.selectbox(key="uvvis_stability_mode").set_value("auto").run()
        self.assertEqual(app.number_input(key="uvvis_sd_threshold").value, 0.002)

    def test_malformed_legacy_sample_log_is_preserved(self):
        self.low_signal_model()
        path = self.root / "sample_results.csv"
        original = "timestamp,concentration\nold,12.0,unexpected\n"
        path.write_text(original, encoding="utf-8")
        sample = UVVisMeasurement(self.config).sample
        sample.results_file = path
        with self.assertRaisesRegex(ValueError, "inconsistent columns"):
            sample.save_result({"timestamp": "new", "concentration": 0.5, "stability_criterion": "sd"})
        self.assertEqual(path.read_text(encoding="utf-8"), original)


class UVVisSDDilutionTests(unittest.TestCase):
    setUp = dilution_helpers.AutoDilutionTests.setUp
    tearDown = dilution_helpers.AutoDilutionTests.tearDown
    write_model = dilution_helpers.AutoDilutionTests.write_model
    run_measurement = dilution_helpers.AutoDilutionTests.run_measurement

    def test_low_direct_measurement_uses_sd_and_leaves_pump_off(self):
        self.runner.uvvis_config.update(stability_mode="auto", sd_threshold=0.002)
        self.env.concentration = 0.5
        self.env.signal = lambda value, index: value + (index % 3 - 1) * 0.1
        self.run_measurement()
        result = self.runner.last_measurement_result
        self.assertTrue(result["dilution_used_for_result"])
        self.assertGreater(result["absorbance_cv"], 5)
        self.assertEqual(result["stability_criterion"], "sd")
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))

    def test_sd_stability_survives_dilution_and_is_logged_per_attempt(self):
        self.runner.uvvis_config.update(stability_mode="sd", sd_threshold=0.002)
        self.env.signal = lambda value, index: value + (index % 3 - 1) * 0.1
        result = self.run_measurement()
        self.assertAlmostEqual(result["Concentration"], 200.0, places=6)
        final = self.runner.last_measurement_result
        self.assertGreater(final["dilution_factor"], 1)
        self.assertTrue(all(a["stability_criterion"] == "sd" for a in final["dilution_history"]))
        self.assertTrue(all(r["Stability Units"] == "AU" for r in self.runner.full_measurement_log))
        self.assertTrue(final["dilution_stop_verified"])
        self.assertEqual(self.env.water_flow, 0)

    def test_unset_sd_limit_blocks_preparation_and_dilution(self):
        self.runner.uvvis_config.update(stability_mode="auto", sd_threshold=None)
        with self.assertRaisesRegex(ValueError, "maximum absorbance SD"):
            self.run_measurement()
        self.runner.protocol_prepare_fn.assert_not_called()
        self.assertEqual(self.env.events, [])


if __name__ == "__main__":
    unittest.main()
