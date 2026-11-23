"""Time-aware rolling-window acceptance using fresh spectra and mocked hardware."""
import contextlib
import io
import json
import math
import os
import unittest
from datetime import datetime, timedelta, timezone

from core.hardware.uvvis_measurement import (
    UVVisMeasurement, MeasurementValidationError, measurement_settings,
    measurement_session_updates,
)
from tests import test_uvvis_integration as helpers
from tests import test_uvvis_dilution as dilution_helpers


VALUES = [0.7059963, 0.723742, 0.7540307, 0.78349, 0.8008229]
TIMESTAMPS = [
    '2026-11-23T11:11:18.321179+00:00',
    '2026-11-23T11:11:26.743605+00:00',
    '2026-11-23T11:11:38.002610+00:00',
    '2026-11-23T11:11:49.342432+00:00',
    '2026-11-23T11:11:57.818310+00:00',
]


class TimedExports(helpers.ExportClock):
    def __init__(self, folder, values, timestamps):
        super().__init__(folder, values)
        self.timestamps = [datetime.fromisoformat(stamp).timestamp() for stamp in timestamps]

    def sleep(self, seconds):
        self.now += seconds
        while self.index < len(self.values) and self.now >= 0.1 + self.timestamps[self.index] - self.timestamps[0]:
            value, timestamp = self.values[self.index], self.timestamps[self.index]
            self.index += 1
            path = self.folder / f'my_acquired_spectra_{self.index:03d}.csv'
            path.write_text(f'Synthetic\nWavelength,Absorbance\n664,{value}\n', encoding='utf-8')
            os.utime(path, (timestamp, timestamp))


class UVVisTrendTests(unittest.TestCase):
    setUp = helpers.UVVisIntegrationTests.setUp
    tearDown = helpers.UVVisIntegrationTests.tearDown
    write_model = helpers.UVVisIntegrationTests.write_model
    runner = helpers.UVVisIntegrationTests.runner

    def configure(self, **overrides):
        self.model.update(slope=0.02, intercept=0.0, range_max=50.0, concentration_unit='mg/L')
        self.write_model()
        self.config.update(n_replicates=5, concentration_unit='mg/L', cv_threshold=10.0,
                           max_extra_measurements=0, spectrum_timeout=15.0,
                           trend_enabled=True, trend_threshold=0.01)
        self.config.update(overrides)

    def test_actual_lab_example_fails_trend_even_when_cv_passes(self):
        self.configure()
        provider = UVVisMeasurement(self.config)
        with TimedExports(self.root, VALUES, TIMESTAMPS).running():
            result = provider.measure()
        self.assertTrue(result['dispersion_accepted'])
        self.assertFalse(result['trend_accepted'])
        self.assertFalse(result['accepted'])
        self.assertAlmostEqual(result['trend_window_duration_s'], 39.497131, places=5)
        self.assertAlmostEqual(result['trend_slope_au_per_min'], 0.1476448026, places=6)
        self.assertAlmostEqual(result['trend_fitted_change_au'], 0.0971924352, places=6)
        self.assertAlmostEqual(result['trend_r2'], 0.9981257665, places=7)
        with self.assertRaisesRegex(MeasurementValidationError, 'fitted drift'):
            provider.require_valid(result)

    def test_decreasing_drift_is_also_rejected(self):
        self.configure()
        sample = UVVisMeasurement(self.config).sample
        result = sample.trend_decision([-value for value in VALUES], TIMESTAMPS)
        self.assertFalse(result['trend_accepted'])
        self.assertEqual(result['trend_direction'], 'decreasing')
        self.assertLess(result['trend_slope_au_per_min'], 0)

    def test_a_small_dip_does_not_hide_an_overall_trend(self):
        self.configure()
        result = UVVisMeasurement(self.config).sample.trend_decision([0.7, 0.73, 0.725, 0.78, 0.8], TIMESTAMPS)
        self.assertFalse(result['trend_accepted'])
        self.assertGreater(result['trend_absolute_change_au'], 0.01)

    def test_small_monotonic_changes_pass_even_with_high_r2(self):
        self.configure()
        sample = UVVisMeasurement(self.config).sample
        elapsed = [(datetime.fromisoformat(t) - datetime.fromisoformat(TIMESTAMPS[0])).total_seconds() for t in TIMESTAMPS]
        result = sample.trend_decision([0.1 + 0.00001 * t for t in elapsed], TIMESTAMPS)
        self.assertTrue(result['trend_accepted'])
        self.assertAlmostEqual(result['trend_r2'], 1.0)

    def test_constant_absorbance_passes_without_inventing_r2(self):
        self.configure()
        result = UVVisMeasurement(self.config).sample.trend_decision([0.5] * 5, TIMESTAMPS)
        self.assertTrue(result['trend_accepted'])
        self.assertEqual(result['trend_direction'], 'flat')
        self.assertEqual(result['trend_fitted_change_au'], 0)
        self.assertIsNone(result['trend_r2'])

    def test_flat_fit_cannot_bypass_excess_sd(self):
        self.configure(stability_mode='sd', sd_threshold=0.002)
        sample = UVVisMeasurement(self.config).sample
        values = [0.4, 0.6, 0.5, 0.6, 0.4]
        stamps = [(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i * 10)).isoformat() for i in range(5)]
        result = sample.window_decision(values, stamps, *sample.calculate_statistics(values))
        self.assertTrue(result['trend_accepted'])
        self.assertFalse(result['dispersion_accepted'])
        self.assertFalse(result['accepted'])

    def test_invalid_timestamps_fail_closed(self):
        self.configure()
        sample = UVVisMeasurement(self.config).sample
        for stamps in ([None] * 5, ['bad'] * 5, TIMESTAMPS[:4], TIMESTAMPS[::-1],
                       [TIMESTAMPS[0]] * 5, [t[:26] for t in TIMESTAMPS]):
            with self.subTest(timestamps=stamps):
                result = sample.trend_decision(VALUES, stamps)
                self.assertFalse(result['trend_accepted'])
                self.assertTrue(result['trend_error'])
                json.dumps(result, allow_nan=False)

    def test_nonfinite_absorbance_is_not_a_valid_trend(self):
        self.configure()
        sample = UVVisMeasurement(self.config).sample
        for bad in (math.inf, math.nan):
            result = sample.trend_decision([0.5, 0.5, bad, 0.5, 0.5], TIMESTAMPS)
            self.assertFalse(result['trend_accepted'])
            self.assertTrue(result['trend_error'])

    def test_last_five_window_recovers_from_a_transient_and_is_logged(self):
        self.configure(max_extra_measurements=4)
        runner = self.runner()
        times = TIMESTAMPS + [(datetime.fromisoformat(TIMESTAMPS[-1]) + timedelta(seconds=10 * i)).isoformat() for i in range(1, 5)]
        with TimedExports(self.root, VALUES + [VALUES[-1]] * 4, times).running():
            result = runner.run_experiment({'substrate_concentration': 50}, objectives=['Concentration'])
        self.assertAlmostEqual(result['Concentration'], VALUES[-1] / 0.02)
        final = runner.last_measurement_result
        self.assertEqual(final['extra_measurements'], 4)
        self.assertEqual(final['replicates'], [VALUES[-1]] * 5)
        self.assertEqual(final['replicate_timestamps'], times[-5:])
        rows = runner.full_measurement_log
        self.assertEqual([r['Used for Stability Window'] for r in rows], [False] * 4 + [True] * 5)
        self.assertTrue(all(r['Trend Accepted'] and r['Drift Limit (AU)'] == 0.01 for r in rows))
        self.assertEqual(runner.measurement_result_fields()['UV/VIS Fitted Drift (AU)'], 0)

    def test_persistent_trend_is_bounded_and_blocks_optimizer_result(self):
        self.configure(max_extra_measurements=2)
        runner = self.runner()
        times = TIMESTAMPS + [(datetime.fromisoformat(TIMESTAMPS[-1]) + timedelta(seconds=10 * i)).isoformat() for i in range(1, 3)]
        runner.protocol_calculate_fn = unittest.mock.Mock()
        with TimedExports(self.root, VALUES + [0.83, 0.86], times).running():
            with self.assertRaisesRegex(MeasurementValidationError, 'fitted drift'):
                runner.run_experiment({}, objectives=['Concentration'])
        runner.protocol_calculate_fn.assert_not_called()
        runner.protocol_cleanup_fn.assert_called_once()
        self.assertEqual(len(runner.full_measurement_log), 7)
        self.assertTrue(all(not row['Accepted'] for row in runner.full_measurement_log))

    def test_invalid_drift_settings_block_preparation(self):
        for limit in (None, 0, -1, math.nan, math.inf, True, 'bad'):
            self.configure(trend_threshold=limit)
            runner = self.runner()
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                runner.run_experiment({}, objectives=['Concentration'])
            runner.protocol_prepare_fn.assert_not_called()

    def test_disabled_check_preserves_dispersion_only_acceptance(self):
        self.configure(trend_enabled=False)
        provider = UVVisMeasurement(self.config)
        with TimedExports(self.root, VALUES, TIMESTAMPS).running():
            result = provider.measure()
        self.assertTrue(result['accepted'])
        self.assertIsNone(result['trend_accepted'])
        self.assertIsNone(result['trend_slope_au_per_min'])

    def test_defaults_and_saved_configuration_round_trip(self):
        defaults = measurement_settings({})['uvvis_config']
        self.assertTrue(defaults['trend_enabled'])
        self.assertEqual(defaults['trend_threshold'], 0.01)
        self.configure(trend_threshold=0.004)
        saved = measurement_settings({'measurement_backend': 'uvvis', 'uvvis_config': self.config})
        restored = measurement_session_updates(json.loads(json.dumps(saved)))
        self.assertTrue(restored['uvvis_trend_enabled'])
        self.assertEqual(restored['uvvis_trend_threshold'], 0.004)

    def test_ui_default_toggle_and_tolerance_persist(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("""import streamlit as st
from core.hardware.measurement_ui import render_measurement_settings
st.session_state.setdefault('measurement_backend', 'uvvis')
render_measurement_settings()
""").run()
        self.assertFalse(app.exception)
        self.assertTrue(app.checkbox(key='uvvis_trend_enabled').value)
        self.assertEqual(app.number_input(key='uvvis_trend_threshold').value, 0.01)
        app.number_input(key='uvvis_trend_threshold').set_value(0.003).run()
        app.checkbox(key='uvvis_trend_enabled').uncheck().run()
        app.checkbox(key='uvvis_trend_enabled').check().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['uvvis_config']['trend_threshold'], 0.003)


class UVVisTrendDilutionTests(unittest.TestCase):
    setUp = dilution_helpers.AutoDilutionTests.setUp
    tearDown = dilution_helpers.AutoDilutionTests.tearDown
    write_model = dilution_helpers.AutoDilutionTests.write_model
    run_measurement = dilution_helpers.AutoDilutionTests.run_measurement

    def test_stable_dilution_starts_separate_windows_and_logs_trend(self):
        self.runner.uvvis_config.update(trend_enabled=True, trend_threshold=0.01, n_replicates=5)
        self.run_measurement()
        result = self.runner.last_measurement_result
        self.assertTrue(result['trend_accepted'])
        self.assertEqual(result['trend_absolute_change_au'], 0)
        self.assertGreater(result['attempt'], 1)
        self.assertTrue(all(a['trend_accepted'] for a in result['dilution_history']))
        self.assertTrue(all(r['Trend Accepted'] for r in self.runner.full_measurement_log))
        self.assertTrue(result['dilution_stop_verified'])

    def test_above_range_drift_does_not_trigger_more_dilution(self):
        self.runner.uvvis_config.update(trend_enabled=True, trend_threshold=0.01, n_replicates=5, cv_threshold=100)
        self.env.signal = lambda value, index: value + index * 2
        with self.assertRaisesRegex(MeasurementValidationError, 'fitted drift'):
            self.run_measurement()
        self.assertTrue(all(flow == 0 for _, flow in self.env.events))
        self.assertEqual(self.env.water_flow, 0)
        self.assertTrue(all(not r['Accepted'] for r in self.runner.full_measurement_log))


if __name__ == '__main__':
    unittest.main()
