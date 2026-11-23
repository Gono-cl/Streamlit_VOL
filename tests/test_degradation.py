"""Concentration-to-degradation calculation and actual acquisition integration."""
import csv
import math
import os
import unittest
from unittest.mock import patch

from core.objectives import calculate_objectives, degradation_percent
from core.hardware.process_adapters import BaseProcessAdapter
from core.campaigns.setup import build_setup_payload, setup_session_updates
from tests import test_uvvis_dilution as dilution_tests
from tests.test_campaign_setup import configured_state


class DegradationCalculationTests(unittest.TestCase):
    def test_zero_partial_and_complete_removal(self):
        for outlet, expected in [(100, 0), (25, 75), (0, 100)]:
            with self.subTest(outlet=outlet):
                self.assertEqual(degradation_percent(outlet, 100), expected)

    def test_concentration_increase_is_visible_as_negative_degradation(self):
        self.assertAlmostEqual(degradation_percent(120, 100), -20)

    def test_invalid_input_is_rejected(self):
        for inlet, outlet in [(0, 1), (-1, 1), (math.nan, 1), (math.inf, 1),
                              (100, -1), (100, math.nan), (100, math.inf), (True, 0), (100, True)]:
            with self.subTest(inlet=inlet, outlet=outlet):
                with self.assertRaises(ValueError):
                    calculate_objectives(outlet, inlet, selected_objectives=["Degradation"])

    def test_yield_and_degradation_remain_different_objectives(self):
        result = calculate_objectives(20, 100, selected_objectives=["Yield", "Degradation", "Concentration"])
        self.assertEqual(result, {"Yield": 20, "Degradation": 80, "Concentration": 20})

    def test_minimization_uses_existing_score_convention(self):
        result = calculate_objectives(20, 100, selected_objectives=["Degradation"], directions={"Degradation": "minimize"})
        self.assertEqual(result, {"Degradation": -80})

    def test_saved_setups_restore_degradation_and_direction(self):
        for mode in ["single", "multi"]:
            state = configured_state(mode)
            state.update(response_to_optimize="Degradation", objectives=["Degradation", "Concentration"],
                         Degradation_direction="maximize", Concentration_direction="minimize")
            payload = build_setup_payload(mode, "MB removal", state, state["run_name"], state["experiment_date"], "")
            restored = setup_session_updates(payload, mode)
            if mode == "single":
                self.assertEqual(restored["single_response_select"], "Degradation")
            else:
                self.assertEqual(restored["multi_objectives_select"], ["Degradation", "Concentration"])
                self.assertEqual(restored["Degradation_direction"], "maximize")


class DegradationAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = dilution_tests.AutoDilutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.runner = self.fixture.runner
        self.runner.protocol_calculate_fn = None
        self.runner.process_adapter = BaseProcessAdapter()

    def run_experiment(self, params=None, objectives=None, directions=None):
        with self.fixture.env.running():
            return self.runner.run_experiment(params or dilution_tests.PARAMS,
                objectives=objectives or ["Degradation", "Concentration"], directions=directions)

    def test_direct_reading_reaches_objective_and_saved_measurement_fields(self):
        self.fixture.env.concentration = 50
        result = self.run_experiment()
        expected = 100 * (1 - 50 / dilution_tests.PARAMS["substrate_concentration"])
        self.assertAlmostEqual(result["Degradation"], expected)
        fields = self.runner.measurement_result_fields()
        self.assertEqual(fields["Concentration Unit"], "mg/L")
        self.assertAlmostEqual(fields["Degradation (%)"], expected)
        self.assertEqual(fields["Outlet Concentration"], 50)
        self.assertTrue(all(flow == 0 for _, flow in self.fixture.env.events))

    def test_dilution_is_corrected_before_degradation_and_raw_csv_retains_it(self):
        result = self.run_experiment()
        inlet = dilution_tests.PARAMS["substrate_concentration"]
        expected = 100 * (1 - 200 / inlet)
        self.assertAlmostEqual(result["Degradation"], expected, places=6)
        self.assertAlmostEqual(result["Concentration"], 200, places=6)
        fields = self.runner.measurement_result_fields()
        self.assertGreater(fields["UV/VIS Dilution Factor"], 1)
        self.assertAlmostEqual(fields["Degradation (%)"], expected)
        rows = self.runner.full_measurement_log
        self.assertTrue(all(row["Degradation (%)"] is None for row in rows[:3]))
        self.assertTrue(all(abs(row["Degradation (%)"] - expected) < 1e-6 for row in rows[3:]))
        old_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.root)
            path = self.runner.save_full_measurements_to_csv("degradation")
            with open(path, newline="", encoding="utf-8") as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual(saved[0]["Degradation (%)"], "")
            self.assertAlmostEqual(float(saved[-1]["Degradation (%)"]), expected)
            self.assertEqual(float(saved[-1]["Inlet Concentration (mg/L)"]), inlet)
        finally:
            os.chdir(old_cwd)

    def test_invalid_inlet_is_rejected_before_preparation(self):
        for inlet in [0, -1, math.nan, math.inf, True]:
            with self.subTest(inlet=inlet):
                self.runner._closed = False
                with self.assertRaises(ValueError):
                    self.run_experiment({**dilution_tests.PARAMS, "substrate_concentration": inlet})
                self.runner.protocol_prepare_fn.assert_not_called()
                self.assertEqual(self.fixture.env.events, [])

    def test_minimized_score_does_not_negate_physical_percentage(self):
        self.fixture.env.concentration = 50
        result = self.run_experiment(directions={"Degradation": "minimize"})
        self.assertLess(result["Degradation"], 0)
        self.assertAlmostEqual(self.runner.measurement_result_fields()["Degradation (%)"], -result["Degradation"])

    def test_acquisition_failure_clears_previous_percentage(self):
        self.fixture.env.concentration = 50
        self.run_experiment()
        self.fixture.env.enabled = False
        with self.assertRaises(TimeoutError):
            self.run_experiment()
        self.assertNotIn("Degradation (%)", self.runner.measurement_result_fields())

    def test_percentage_is_not_reused_when_a_different_response_is_selected(self):
        self.fixture.env.concentration = 50
        self.run_experiment()
        self.run_experiment(objectives=["Concentration"])
        self.assertNotIn("Degradation (%)", self.runner.measurement_result_fields())

    def test_full_simulation_computes_same_response_without_hardware(self):
        self.runner.simulation_mode = "full"
        with patch.object(self.runner, "synthetic_raw_area", return_value=20):
            result = self.run_experiment({"substrate_concentration": 100})
        self.assertEqual(result["Degradation"], 80)
        self.assertEqual(self.runner.measurement_result_fields()["Degradation (%)"], 80)
        self.runner.protocol_prepare_fn.assert_not_called()
        self.assertEqual(self.fixture.env.events, [])

    def test_custom_adapter_inlet_parameter_is_used(self):
        self.runner.measurement_backend = "opc"
        self.runner.process_adapter = BaseProcessAdapter({"substrate_key": "feed"})
        with patch.object(self.runner, "collect_measurements", return_value=25):
            result = self.run_experiment({"feed": 100})
        self.assertEqual(result["Degradation"], 75)
        self.assertEqual(self.runner.measurement_result_fields()["Inlet Concentration"], 100)


if __name__ == "__main__":
    unittest.main()
