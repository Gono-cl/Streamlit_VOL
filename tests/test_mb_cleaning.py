"""Water/ethanol/water cleaning and mode interlocks with in-memory equipment."""

import contextlib
import io
import math
import unittest
from unittest.mock import Mock, call, patch

import requests

from core.hardware.opc_communication import OPCClient
from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


PARAMS = {"current_density": 3.028, "applied_charge": 6.753,
          "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
          "cell": "EC3", "electrolyte": "NaCl"}


class WaterCleaningTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        sleep_patch = patch.object(protocol.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        log_patch = patch.object(protocol, "_log_operating_point")
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.runner, self.sdk = make_runner(5000, initialized=False)

    def prepared_and_stopped(self):
        protocol.prepare_hardware(self.runner, PARAMS)
        protocol.stop_pumps(self.runner, PARAMS)
        self.runner._experiment_completed = True
        self.sleep.reset_mock()
        self.runner.opc.write_value.reset_mock()

    def cleaning_starts(self):
        return [args for args in self.runner.opc.write_value.call_args_list
                if args.args[0] == protocol.PUMP_CLEANING_TAG and args.args[1] > 0]

    def assert_mode(self, mode):
        expected = protocol.VALVE_ELECTROLYSIS_TAGS if mode == "electrolysis" else protocol.VALVE_CLEANING_TAGS
        for tag, value in expected:
            self.assertEqual(self.runner.opc.values[tag], value)

    def configure_runner_loop(self, result=50.0, failure=None):
        runner = self.runner
        runner._closed = False
        runner.measurement_backend = "opc"
        runner.use_autosampler = False
        runner.protocol_prepare_fn = protocol.prepare_hardware
        runner.protocol_calculate_fn = lambda *args: {"Yield": result}
        runner.protocol_stop_pumps_fn = protocol.stop_pumps
        runner.protocol_cleanup_fn = protocol.cleanup
        runner.collect_measurements = Mock(return_value=result, side_effect=failure)

    def test_ten_millilitre_sequence_preserves_syringe_and_uses_correct_pump_units(self):
        self.prepared_and_stopped()
        before = self.sdk.volume
        moves = list(self.sdk.moves)
        contents = self.runner._mb_spm_contents

        stages = iter([(90.0, 2000.0, 0.0, "electrolysis"),
                       (120.0, 0.0, 2.0, "cleaning"),
                       (90.0, 2000.0, 0.0, "electrolysis")])

        def during_clean(seconds):
            duration, water, ethanol, mode = next(stages)
            self.assertEqual(seconds, duration)
            self.assert_mode(mode)
            self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], water)
            self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], ethanol)
            for tag, value in protocol.UVVIS_MEASUREMENT_TAGS:
                self.assertEqual(self.runner.opc.values[tag], value)
            self.assertEqual(self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG], 3)
            self.assertEqual(self.runner.cell_valve.get_position(), 3)
            for tag, value in protocol.POWER_OFF_TAGS:
                self.assertEqual(self.runner.opc.values[tag], value)
            self.assertEqual(self.runner.opc.values[protocol.PUMP_MB_TAG], 0)
            self.assertEqual(self.runner.opc.values[protocol.PUMP_ANALYTICAL_DILUTION_TAG], 0)
            self.assertIsNone(self.sdk.pending)
        self.sleep.side_effect = during_clean
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.sleep.call_args_list, [call(90.0), call(120.0), call(90.0)])
        self.assertEqual(len(self.cleaning_starts()), 1)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], 0)
        for tag, value in protocol.UVVIS_WASTE_TAGS:
            self.assertEqual(self.runner.opc.values[tag], value)
        self.assert_mode("electrolysis")
        self.assertEqual(self.sdk.moves, moves)
        self.assertEqual(self.sdk.volume, before)
        self.assertEqual(self.runner._mb_spm_contents, contents)

    def test_explicit_legacy_overrides_set_common_flow_and_ethanol_duration(self):
        self.prepared_and_stopped()
        self.runner.adapter_config = {"cleaning_flow_ml_min": 1.5, "cleaning_duration_s": 20}
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.sleep.call_args_list, [call(90.0), call(20.0), call(90.0)])
        self.assertEqual(self.cleaning_starts()[0].args[1], 1.5)
        water = [c.args[1] for c in self.runner.opc.write_value.call_args_list
                 if c.args[0] == protocol.PUMP_WATER_TAG and c.args[1] > 0]
        self.assertEqual(water, [1500.0, 1500.0])

    def test_pumps_never_overlap_and_valves_move_only_with_both_stopped(self):
        self.prepared_and_stopped()
        original = self.runner.opc.write_value.side_effect
        valve_tags = dict(protocol.VALVE_ELECTROLYSIS_TAGS + protocol.UVVIS_MEASUREMENT_TAGS)
        starts = []

        def write(tag, value, **kwargs):
            if tag in valve_tags or (tag in (protocol.PUMP_WATER_TAG, protocol.PUMP_CLEANING_TAG) and value > 0):
                self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], 0)
                self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
            if tag in (protocol.PUMP_WATER_TAG, protocol.PUMP_CLEANING_TAG) and value > 0:
                starts.append((tag, value))
            return original(tag, value, **kwargs)

        self.runner.opc.write_value.side_effect = write
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(starts, [(protocol.PUMP_WATER_TAG, 2000),
                                  (protocol.PUMP_CLEANING_TAG, 2),
                                  (protocol.PUMP_WATER_TAG, 2000)])

    def test_each_stage_can_be_configured_and_explicit_ethanol_duration_wins(self):
        self.prepared_and_stopped()
        self.runner.adapter_config = {
            "cleaning_flow_ml_min": 1.1, "cleaning_duration_s": 12,
            "cleaning_water_pre_flow_ml_min": 1.25, "cleaning_water_pre_duration_s": 40,
            "cleaning_ethanol_flow_ml_min": 2.5, "cleaning_ethanol_duration_s": 10,
            "cleaning_water_post_flow_ml_min": 3.0, "cleaning_water_post_duration_s": 80,
        }
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.sleep.call_args_list, [call(40), call(10), call(80)])
        starts = [c.args for c in self.runner.opc.write_value.call_args_list
                  if c.args[0] in (protocol.PUMP_WATER_TAG, protocol.PUMP_CLEANING_TAG) and c.args[1] > 0]
        self.assertEqual(starts, [(protocol.PUMP_WATER_TAG, 1250),
                                  (protocol.PUMP_CLEANING_TAG, 2.5),
                                  (protocol.PUMP_WATER_TAG, 3000)])

    def test_invalid_later_stage_is_rejected_before_any_cleaning_flow(self):
        self.prepared_and_stopped()
        for config in ({"cleaning_water_post_duration_s": math.nan},
                       {"cleaning_ethanol_flow_ml_min": math.inf},
                       {"cleaning_water_pre_flow_ml_min": True},
                       {"cleaning_water_post_flow_ml_min": -1},
                       {"cleaning_ethanol_duration_s": None}):
            with self.subTest(config=config):
                self.runner.adapter_config = config
                with self.assertRaises(ValueError):
                    protocol.cleanup(self.runner, PARAMS)
        self.assertFalse(any(c.args[0] in (protocol.PUMP_WATER_TAG, protocol.PUMP_CLEANING_TAG) and c.args[1] > 0
                             for c in self.runner.opc.write_value.call_args_list))

    def test_interruption_at_any_stage_stops_both_pumps_and_skips_later_stages(self):
        for failing_stage in (1, 2, 3):
            with self.subTest(stage=failing_stage):
                self.sleep.side_effect = None
                self.runner, self.sdk = make_runner(5000, initialized=False)
                self.prepared_and_stopped()
                count = 0

                def wait(seconds):
                    nonlocal count
                    count += 1
                    if count == failing_stage:
                        raise KeyboardInterrupt("Interrupted rinse")

                self.sleep.side_effect = wait
                with self.assertRaisesRegex(KeyboardInterrupt, "Interrupted rinse"):
                    protocol.cleanup(self.runner, PARAMS)
                self.assertEqual(count, failing_stage)
                self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], 0)
                self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
                self.assertFalse(self.runner._mb_cleaning_active)
                self.assertFalse(self.runner._mb_feed_shutdown_confirmed)
                self.assert_mode("electrolysis")
                for tag, value in protocol.UVVIS_WASTE_TAGS:
                    self.assertEqual(self.runner.opc.values[tag], value)

    def test_unconfirmed_water_stop_prevents_ethanol_and_all_valve_moves(self):
        for failure in ("write", "readback"):
            with self.subTest(failure=failure):
                self.runner, self.sdk = make_runner(5000, initialized=False)
                self.prepared_and_stopped()
                original_write = self.runner.opc.write_value.side_effect
                original_read = self.runner.opc.read_value.side_effect
                started = False
                later_valve_writes = []
                valve_tags = dict(protocol.VALVE_ELECTROLYSIS_TAGS + protocol.UVVIS_MEASUREMENT_TAGS)

                def write(tag, value, **kwargs):
                    nonlocal started
                    if tag == protocol.PUMP_WATER_TAG:
                        if value > 0:
                            started = True
                        elif started and failure == "write":
                            return False
                    if started and tag in valve_tags:
                        later_valve_writes.append((tag, value))
                    return original_write(tag, value, **kwargs)

                def read(tag, **kwargs):
                    if started and failure == "readback" and tag == protocol.PUMP_WATER_TAG:
                        return 2000
                    return original_read(tag, **kwargs)

                self.runner.opc.write_value.side_effect = write
                self.runner.opc.read_value.side_effect = read
                with self.assertRaisesRegex(RuntimeError, "shutdown is uncertain"):
                    protocol.cleanup(self.runner, PARAMS)
                self.assertEqual(later_valve_writes, [])
                self.assertEqual(self.cleaning_starts(), [])
                self.assertTrue(self.runner._mb_cleaning_active)
                self.assertFalse(self.runner._mb_feed_shutdown_confirmed)
                self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)

    def test_lost_ethanol_start_reply_still_stops_both_pumps(self):
        self.prepared_and_stopped()
        original = self.runner.opc.write_value.side_effect

        def write(tag, value, **kwargs):
            result = original(tag, value, **kwargs)
            if tag == protocol.PUMP_CLEANING_TAG and value > 0:
                return False  # Device may have started even though its reply failed.
            return result

        self.runner.opc.write_value.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.sleep.call_args_list, [call(90)])
        self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], 0)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)
        self.assert_mode("electrolysis")

    def test_busy_spm_blocks_all_cleaning_flow(self):
        self.prepared_and_stopped()
        self.sdk.pending = 1000
        self.sdk.never_complete = True
        with self.assertRaises(RuntimeError):
            protocol.cleanup(self.runner, PARAMS)
        self.assertFalse(any(c.args[0] in (protocol.PUMP_WATER_TAG, protocol.PUMP_CLEANING_TAG) and c.args[1] > 0
                             for c in self.runner.opc.write_value.call_args_list))

    def test_each_experiment_restores_and_checks_electrolysis_before_feeding(self):
        self.prepared_and_stopped()
        protocol.cleanup(self.runner, PARAMS)
        # Simulate a manual switch back to cleaning mode between experiments.
        self.runner.opc.values.update(dict(protocol.VALVE_CLEANING_TAGS))
        original = self.runner.opc.write_value.side_effect

        def write(tag, value, **kwargs):
            if tag in dict(protocol.FEED_STOP_TAGS) and value > 0:
                self.assert_mode("electrolysis")
                for valve_tag, expected in protocol.VALVE_ELECTROLYSIS_TAGS:
                    self.assertIn(call(valve_tag, timeout=protocol.OPC_CONTROL_TIMEOUT_S),
                                  self.runner.opc.read_value.call_args_list)
            return original(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write
        protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_mode("electrolysis")
        self.assertEqual(self.sdk.events.count(("home", 1, False)), 1)

    def test_unconfirmed_electrolysis_mode_blocks_reaction_feeds_and_power(self):
        first_tag = protocol.VALVE_ELECTROLYSIS_TAGS[0][0]
        wrong_value = 1 - dict(protocol.VALVE_ELECTROLYSIS_TAGS)[first_tag]
        original = self.runner.opc.read_value.side_effect
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: wrong_value if tag == first_tag else original(tag, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "electrolysis valve commands"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()
        self.assertFalse(any(args.args[0] in dict(protocol.FEED_STOP_TAGS) and args.args[1] > 0
                             for args in self.runner.opc.write_value.call_args_list))
        self.assertEqual(self.cleaning_starts(), [])

    def test_boolean_valve_and_power_readbacks_are_supported(self):
        original = self.runner.opc.read_value.side_effect
        boolean_tags = dict(protocol.VALVE_ELECTROLYSIS_TAGS + protocol.POWER_OFF_TAGS)
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: (
            bool(original(tag, **kwargs)) if tag in boolean_tags else original(tag, **kwargs))
        self.prepared_and_stopped()
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(len(self.cleaning_starts()), 1)
        self.assert_mode("electrolysis")

    def test_ethanol_route_write_failure_blocks_ethanol_and_final_water(self):
        self.prepared_and_stopped()
        first_tag = protocol.VALVE_CLEANING_TAGS[0][0]
        cleaning_value = dict(protocol.VALVE_CLEANING_TAGS)[first_tag]
        original = self.runner.opc.write_value.side_effect
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: (
            False if tag == first_tag and value == cleaning_value else original(tag, value, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.cleaning_starts(), [])
        self.assertEqual(sum(c.args[0] == protocol.PUMP_WATER_TAG and c.args[1] > 0
                             for c in self.runner.opc.write_value.call_args_list), 1)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assert_mode("electrolysis")

    def test_unconfirmed_cell_inlet_blocks_cleaning_pump(self):
        self.prepared_and_stopped()
        original = self.runner.opc.read_value.side_effect
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: (
            1 if tag == protocol.CELL_INLET_VALVE_TAG else original(tag, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "cell inlet port 3"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.cleaning_starts(), [])
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)

    def test_interruption_always_stops_cleaning_pump_and_restores_mode(self):
        self.prepared_and_stopped()
        self.sleep.side_effect = KeyboardInterrupt("Interrupted cleaning")
        with self.assertRaises(KeyboardInterrupt):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertFalse(self.runner._mb_cleaning_active)
        self.assert_mode("electrolysis")

    def test_failed_ethanol_stop_does_not_switch_valves_with_pump_still_running(self):
        self.prepared_and_stopped()
        original = self.runner.opc.write_value.side_effect
        started = False

        def write(tag, value, **kwargs):
            nonlocal started
            if tag == protocol.PUMP_CLEANING_TAG:
                if value > 0:
                    started = True
                elif started:
                    return False
            return original(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 2)
        self.assertTrue(self.runner._mb_cleaning_active)
        self.assert_mode("cleaning")
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)

    def test_invalid_cleaning_settings_never_start_the_pump(self):
        self.prepared_and_stopped()
        for config in ({"cleaning_duration_s": 0}, {"cleaning_flow_ml_min": math.nan},
                       {"cleaning_flow_ml_min": 0}):
            with self.subTest(config=config):
                self.runner.adapter_config = config
                with self.assertRaises(ValueError):
                    protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.cleaning_starts(), [])

    def test_successful_runner_cleans_after_every_experiment_without_rehoming(self):
        self.configure_runner_loop()
        for _ in range(2):
            self.assertEqual(self.runner.run_experiment(PARAMS, objectives=["Yield"]), {"Yield": 50.0})
        self.assertEqual(len(self.cleaning_starts()), 2)
        self.assertEqual(self.sdk.events.count(("home", 1, False)), 1)
        self.assert_mode("electrolysis")
        self.assertTrue(self.sdk.connected)

    def test_failed_experiment_shuts_down_without_starting_cleaning(self):
        self.configure_runner_loop(failure=ValueError("Measurement failed"))
        with self.assertRaisesRegex(ValueError, "Measurement failed"):
            self.runner.run_experiment(PARAMS, objectives=["Yield"])
        self.assertEqual(self.cleaning_starts(), [])
        self.assertFalse(self.runner._experiment_completed)
        self.assertFalse(self.sdk.connected)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)

    def test_failed_second_experiment_does_not_reuse_previous_success_for_cleaning(self):
        self.configure_runner_loop()
        self.runner.run_experiment(PARAMS, objectives=["Yield"])
        self.runner.collect_measurements.side_effect = ValueError("Second measurement failed")
        with self.assertRaisesRegex(ValueError, "Second measurement failed"):
            self.runner.run_experiment(PARAMS, objectives=["Yield"])
        self.assertEqual(len(self.cleaning_starts()), 1)
        self.assertFalse(self.runner._experiment_completed)

    def test_uncertain_feed_shutdown_blocks_cleaning_and_releases_connections(self):
        self.configure_runner_loop()
        original = self.runner.opc.write_value.side_effect
        started = False

        def write(tag, value, **kwargs):
            nonlocal started
            if tag == protocol.PUMP_MB_TAG:
                if value > 0:
                    started = True
                elif started:
                    return False
            return original(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "shutdown is uncertain"):
            self.runner.run_experiment(PARAMS, objectives=["Yield"])
        self.assertEqual(self.cleaning_starts(), [])
        self.assertFalse(self.sdk.connected)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)

    def test_opc_readbacks_have_a_timeout_without_changing_existing_reads(self):
        opc = OPCClient("http://example.invalid")
        response = Mock(text='{"data": [{"Value": 1}]}')
        with patch("core.hardware.opc_communication.requests.get", return_value=response) as get:
            self.assertEqual(opc.read_value("valve-tag", timeout=2.0), 1)
            self.assertEqual(get.call_args.kwargs, {"timeout": 2.0})
            self.assertEqual(opc.read_value("legacy-tag"), 1)
            self.assertEqual(get.call_args.kwargs, {})
        with patch("core.hardware.opc_communication.requests.get", side_effect=requests.Timeout("Timed out")):
            self.assertIsNone(opc.read_value("valve-tag", timeout=2.0))


if __name__ == "__main__":
    unittest.main()
