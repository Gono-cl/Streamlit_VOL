"""Check MB waste diversion and volume-based flushing with simulated equipment."""

import contextlib
import io
import math
import unittest
from unittest.mock import Mock, patch

from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


PARAMS = {"current_density": 3.028, "applied_charge": 6.753,
          "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
          "cell": "EC3", "electrolyte": "NaCl"}


class UVVisRoutingTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.sleep = stack.enter_context(patch.object(protocol.time, "sleep"))
        stack.enter_context(patch.object(protocol, "_log_operating_point"))
        self.runner, self.sdk = make_runner(5000, initialized=False)

    def assert_commands(self, commands):
        for tag, value in commands:
            self.assertEqual(self.runner.opc.values[tag], value)

    def test_waste_during_both_startup_waits_then_two_ml_through_uvvis(self):
        op = protocol._compute_operating_point(PARAMS)
        waits = []
        original_write = self.runner.opc.write_value.side_effect

        def write(tag, value, **kwargs):
            if tag in dict(protocol.FEED_STOP_TAGS) and value > 0:
                self.assert_commands(protocol.UVVIS_WASTE_TAGS)
                self.assert_commands(protocol.VALVE_ELECTROLYSIS_TAGS)
            if tag in dict(protocol.UVVIS_MEASUREMENT_TAGS) and value == 1:
                self.assertEqual(waits, [1.0, op["pre_echem_flush_time_s"], 3.0, op["measurement_ready_time_s"]])
            return original_write(tag, value, **kwargs)

        def wait(seconds):
            self.assert_commands(protocol.VALVE_ELECTROLYSIS_TAGS)
            self.assertEqual(self.runner.cell_valve.get_position(), 3)
            self.assertEqual(self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG], 3)
            if len(waits) < 4:
                self.assert_commands(protocol.UVVIS_WASTE_TAGS)
            else:
                self.assert_commands(protocol.UVVIS_MEASUREMENT_TAGS)
                self.assertEqual(self.runner.opc.values[protocol.PUMP_ANALYTICAL_DILUTION_TAG], 0)
            waits.append(seconds)

        self.runner.opc.write_value.side_effect = write
        self.sleep.side_effect = wait
        prepared = protocol.prepare_hardware(self.runner, PARAMS)
        self.assertEqual(len(waits), 5)
        self.assertAlmostEqual(waits[-1], 120.0 / op["total_flow_ml_min"])
        self.assertEqual(prepared["uvvis_flush_volume_ml"], 2.0)
        self.assertAlmostEqual(prepared["uvvis_flush_time_s"], waits[-1])

    def test_diversion_commands_preserve_electrolysis_valve_and_cell_selectors(self):
        protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.opc.write_value.reset_mock()
        self.runner.cell_valve.move_to.reset_mock()
        protocol._set_uvvis_route(self.runner, "waste")
        protocol._set_uvvis_route(self.runner, "uvvis")
        writes = self.runner.opc.write_value.call_args_list
        self.assertEqual([c.args for c in writes], list(protocol.UVVIS_WASTE_TAGS + protocol.UVVIS_MEASUREMENT_TAGS))
        self.runner.cell_valve.move_to.assert_not_called()
        self.assert_commands(protocol.VALVE_ELECTROLYSIS_TAGS)

    def test_configurable_flush_volume_scales_with_reactor_flow(self):
        for charge, volume in [(4.0, 0.5), (6.753, 1.0), (10.0, 2.0)]:
            with self.subTest(charge=charge, volume=volume):
                self.runner, self.sdk = make_runner(5000, initialized=False)
                self.runner.adapter_config = {"uvvis_flush_volume_ml": volume}
                self.sleep.reset_mock()
                op = protocol.prepare_hardware(self.runner, {**PARAMS, "applied_charge": charge})
                self.assertAlmostEqual(self.sleep.call_args.args[0], 60.0 * volume / op["total_flow_ml_min"])
                protocol.stop_pumps(self.runner, PARAMS)

    def test_invalid_flush_volume_is_rejected_before_hardware_access(self):
        for volume in [0, -1, math.nan, math.inf, True]:
            with self.subTest(volume=volume):
                self.runner.adapter_config = {"uvvis_flush_volume_ml": volume}
                with self.assertRaisesRegex(ValueError, "flush volume"):
                    protocol.prepare_hardware(self.runner, PARAMS)
                self.assertEqual(self.sdk.events, [])
                self.runner.opc.write_value.assert_not_called()
                self.runner.turn_on_power_supply.assert_not_called()

    def test_unconfirmed_uvvis_route_blocks_readiness_and_stops_feeds(self):
        original = self.runner.opc.write_value.side_effect
        uvvis_tags = dict(protocol.UVVIS_MEASUREMENT_TAGS)

        def write(tag, value, **kwargs):
            if tag in uvvis_tags and value == 1:
                return True  # Accepted reply, but the command item remains at waste.
            return original(tag, value, **kwargs)

        self.runner.opc.write_value.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "UV/VIS uvvis route commands"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_commands(protocol.FEED_STOP_TAGS)
        self.assert_commands(protocol.UVVIS_WASTE_TAGS)
        self.assertIsNone(self.sdk.pending)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)

    def test_failed_waste_command_does_not_prevent_other_shutdown_attempts(self):
        protocol.prepare_hardware(self.runner, PARAMS)
        original = self.runner.opc.write_value.side_effect
        waste_tags = dict(protocol.UVVIS_WASTE_TAGS)
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: (
            False if tag in waste_tags and value == 0 else original(tag, value, **kwargs))
        self.runner.turn_off_power_supply.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "shutdown is uncertain"):
            protocol.stop_pumps(self.runner, PARAMS)
        self.runner.turn_off_power_supply.assert_called_once()
        self.assert_commands(protocol.FEED_STOP_TAGS)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertIsNone(self.sdk.pending)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)

    def test_acquisition_follows_flush_and_shutdown_diverts_before_power_off(self):
        runner = self.runner
        events = []
        original_off = runner.turn_off_power_supply.side_effect

        def off():
            if "measurement" in events:
                self.assert_commands(protocol.UVVIS_WASTE_TAGS)
                events.append("power_off")
            return original_off()

        def measure(**kwargs):
            self.assert_commands(protocol.UVVIS_MEASUREMENT_TAGS)
            self.assertEqual(len(self.sleep.call_args_list), 5)
            events.append("measurement")
            return 50.0

        runner.turn_off_power_supply.side_effect = off
        runner._closed = False
        runner.measurement_backend = "opc"
        runner.use_autosampler = False
        runner.protocol_prepare_fn = protocol.prepare_hardware
        runner.protocol_calculate_fn = lambda *args: {"Yield": 50.0}
        runner.protocol_stop_pumps_fn = protocol.stop_pumps
        runner.protocol_cleanup_fn = protocol.cleanup
        runner.collect_measurements = Mock(side_effect=measure)
        self.assertEqual(runner.run_experiment(PARAMS, objectives=["Yield"]), {"Yield": 50.0})
        self.assertEqual(events[0:2], ["measurement", "power_off"])
        self.assert_commands(protocol.UVVIS_WASTE_TAGS)
        self.assert_commands(protocol.VALVE_ELECTROLYSIS_TAGS)
        self.assertEqual(runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)


if __name__ == "__main__":
    unittest.main()
