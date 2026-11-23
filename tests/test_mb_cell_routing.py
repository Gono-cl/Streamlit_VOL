"""Exercise inlet/outlet cell routing with in-memory OPC and USB devices."""
import contextlib
import io
import unittest
from unittest.mock import Mock, call, patch

from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


PARAMS = {"current_density": 3.028, "applied_charge": 6.753,
          "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
          "cell": "EC3", "electrolyte": "NaCl"}


class CellRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.sleep = self.stack.enter_context(patch.object(protocol.time, "sleep"))
        self.stack.enter_context(patch.object(protocol, "_log_operating_point"))
        self.runner, self.sdk = make_runner(5000, initialized=False)

    def assert_route(self, port):
        self.assertEqual(self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG], port)
        self.assertEqual(self.runner.cell_valve.get_position(), port)

    def assert_no_reaction_started(self):
        self.runner.turn_on_power_supply.assert_not_called()
        self.assertFalse(any(args.args[0] in dict(protocol.FEED_STOP_TAGS) and args.args[1] > 0
                             for args in self.runner.opc.write_value.call_args_list))

    def prepare_and_stop(self, params=None):
        protocol.prepare_hardware(self.runner, params or PARAMS)
        protocol.stop_pumps(self.runner, params or PARAMS)
        self.runner._experiment_completed = True
        self.runner.opc.write_value.reset_mock()

    def test_all_cells_have_matching_inlet_outlet_and_power_channel(self):
        for port in (1, 2, 3):
            with self.subTest(port=port):
                self.runner, self.sdk = make_runner(5000, initialized=False)
                protocol.prepare_hardware(self.runner, {**PARAMS, "cell": f"EC{port}"})
                self.assert_route(port)
                self.assertIn(call(protocol.CELL_INLET_VALVE_TAG, port, timeout=protocol.OPC_CONTROL_TIMEOUT_S),
                              self.runner.opc.write_value.call_args_list)
                for channel, tag in protocol.BKK_CHANNEL_TAGS.items():
                    self.assertEqual(self.runner.opc.values[tag], int(channel == port))

    def test_selectors_move_only_while_power_and_all_pumps_are_stopped(self):
        original_write = self.runner.opc.write_value.side_effect
        original_move = self.runner.cell_valve.move_to.side_effect
        moves = []

        def check_stopped():
            for tag, value in protocol.POWER_OFF_TAGS + protocol.FEED_STOP_TAGS:
                self.assertEqual(self.runner.opc.values[tag], value)
            self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
            self.assertIsNone(self.sdk.pending)

        def move(port):
            check_stopped()
            moves.append(("outlet", port))
            return original_move(port)

        def write(tag, value, **kwargs):
            if tag == protocol.CELL_INLET_VALVE_TAG:
                check_stopped()
                moves.append(("inlet", value))
            if tag in dict(protocol.FEED_STOP_TAGS) and value > 0:
                self.assert_route(3)
            return original_write(tag, value, **kwargs)

        self.runner.cell_valve.move_to.side_effect = move
        self.runner.opc.write_value.side_effect = write
        protocol.prepare_hardware(self.runner, PARAMS)
        self.assertEqual(moves, [("outlet", 3), ("inlet", 3)])

    def test_inlet_write_failure_blocks_feeds_and_power(self):
        original = self.runner.opc.write_value.side_effect
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: (
            False if tag == protocol.CELL_INLET_VALVE_TAG else original(tag, value, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "Required OPC write failed"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_no_reaction_started()

    def test_inlet_readback_mismatch_blocks_feeds_and_power(self):
        original = self.runner.opc.read_value.side_effect
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: (
            1 if tag == protocol.CELL_INLET_VALVE_TAG else original(tag, **kwargs))
        with self.assertRaisesRegex(RuntimeError, "cell inlet port 3"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_no_reaction_started()

    def test_wrong_outlet_position_blocks_feeds_and_power(self):
        self.runner.cell_valve.move_to.side_effect = lambda port: 2
        self.runner.cell_valve.get_position.side_effect = lambda: 2
        with self.assertRaisesRegex(RuntimeError, "AMF cell outlet port 3"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_no_reaction_started()

    def test_unavailable_outlet_position_blocks_feeds_and_power(self):
        self.runner.cell_valve.get_position.side_effect = OSError("USB unavailable")
        with self.assertRaisesRegex(RuntimeError, "AMF cell outlet port 3.*USB unavailable"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assert_no_reaction_started()

    def test_route_is_rechecked_before_power_after_flushing(self):
        op = protocol._compute_operating_point(PARAMS)

        def sleep(seconds):
            if seconds == op["pre_echem_flush_time_s"]:
                self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG] = 1
        self.sleep.side_effect = sleep
        with self.assertRaisesRegex(RuntimeError, "cell inlet port 3"):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()
        for tag, value in protocol.FEED_STOP_TAGS:
            self.assertEqual(self.runner.opc.values[tag], value)

    def test_direct_routing_request_is_blocked_if_a_feed_is_running(self):
        self.prepare_and_stop()
        self.runner.opc.values[protocol.PUMP_MB_TAG] = 100
        self.runner.cell_valve.move_to.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "power and pumps stopped before cell routing"):
            protocol._select_cell(self.runner, 2)
        self.runner.cell_valve.move_to.assert_not_called()
        self.runner.opc.write_value.assert_not_called()

    def test_invalid_ports_do_not_move_either_selector(self):
        for port in (0, 4, True, "2", 2.5):
            with self.subTest(port=port), self.assertRaises(ValueError):
                protocol._select_cell(self.runner, port)
        self.runner.cell_valve.move_to.assert_not_called()
        self.runner.opc.write_value.assert_not_called()

    def test_unknown_cleaning_cell_cannot_start_the_water_pump(self):
        self.prepare_and_stop()
        with self.assertRaisesRegex(ValueError, "requires the selected cell"):
            protocol.cleanup(self.runner, {})
        self.runner.opc.write_value.assert_not_called()

    def test_busy_syringe_blocks_direct_selector_changes(self):
        self.prepare_and_stop()
        self.sdk.pending = 1000
        self.sdk.never_complete = True
        self.runner.cell_valve.move_to.reset_mock()
        with self.assertRaises(RuntimeError):
            protocol._select_cell(self.runner, 2)
        self.runner.cell_valve.move_to.assert_not_called()
        self.runner.opc.write_value.assert_not_called()

    def test_cleaning_preserves_both_selectors_for_every_cell(self):
        for port in (1, 2, 3):
            with self.subTest(port=port):
                self.runner, self.sdk = make_runner(5000, initialized=False)
                params = {**PARAMS, "cell": f"EC{port}"}
                self.prepare_and_stop(params)
                move_count = self.runner.cell_valve.move_to.call_count
                self.sleep.reset_mock()

                def during_clean(seconds):
                    self.assert_route(port)
                    water = self.runner.opc.values[protocol.PUMP_WATER_TAG]
                    ethanol = self.runner.opc.values[protocol.PUMP_CLEANING_TAG]
                    self.assertIn((water, ethanol), [(2000.0, 0), (0, 2.0)])
                self.sleep.side_effect = during_clean
                protocol.cleanup(self.runner, params)
                self.sleep.side_effect = None
                self.assert_route(port)
                self.assertEqual(self.runner.cell_valve.move_to.call_count, move_count)
                self.assertFalse(any(args.args[0] == protocol.CELL_INLET_VALVE_TAG
                                     for args in self.runner.opc.write_value.call_args_list))
                self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)

    def test_outlet_mismatch_prevents_cleaning_without_rerouting(self):
        self.prepare_and_stop()
        self.runner.cell_valve.position = 2
        moves = self.runner.cell_valve.move_to.call_count
        with self.assertRaisesRegex(RuntimeError, "AMF cell outlet port 3"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.runner.cell_valve.move_to.call_count, moves)
        self.assertFalse(any(args.args[0] == protocol.PUMP_CLEANING_TAG and args.args[1] > 0
                             for args in self.runner.opc.write_value.call_args_list))

    def test_route_change_during_cleaning_rejects_success_and_stops_water(self):
        self.prepare_and_stop()

        def during_clean(seconds):
            self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG] = 1
        self.sleep.side_effect = during_clean
        with self.assertRaisesRegex(RuntimeError, "cell inlet port 3"):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)

    def test_legacy_water_port_setting_cannot_redirect_the_selected_cell(self):
        self.prepare_and_stop()
        self.runner.adapter_config = {"cleaning_water_port": 1}
        protocol.cleanup(self.runner, PARAMS)
        self.assert_route(3)
        self.assertFalse(any(args.args[0] == protocol.CELL_INLET_VALVE_TAG
                             for args in self.runner.opc.write_value.call_args_list))

    def test_full_simulation_does_not_select_ports_or_clean(self):
        self.runner.simulation_mode = "full"
        protocol.prepare_hardware(self.runner, PARAMS)
        protocol.cleanup(self.runner, PARAMS)
        self.runner.cell_valve.move_to.assert_not_called()
        self.runner.cell_valve.get_position.assert_not_called()
        self.runner.opc.write_value.assert_not_called()
        self.runner.opc.read_value.assert_not_called()

    def test_reproducibility_cleaning_uses_three_stages_and_retains_cell_three(self):
        self.prepare_and_stop()
        self.runner.running_protocol_module = protocol
        moves = self.runner.cell_valve.move_to.call_count
        self.runner.cleaning_electrochemical_cell()
        self.assert_route(3)
        self.assertEqual(self.runner.cell_valve.move_to.call_count, moves)
        self.assertFalse(any(args.args[0] == protocol.CELL_INLET_VALVE_TAG
                             for args in self.runner.opc.write_value.call_args_list))
        flows = [args.args[1] for args in self.runner.opc.write_value.call_args_list
                 if args.args[0] == protocol.PUMP_CLEANING_TAG and args.args[1] > 0]
        self.assertEqual(flows, [2.0])
        water = [c.args[1] for c in self.runner.opc.write_value.call_args_list
                 if c.args[0] == protocol.PUMP_WATER_TAG and c.args[1] > 0]
        self.assertEqual(water, [2000.0, 2000.0])
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)

    def test_additional_cleaning_cannot_fall_back_after_an_mb_error(self):
        self.prepare_and_stop()
        self.runner.running_protocol_module = protocol
        self.runner._experiment_completed = False
        with self.assertRaisesRegex(RuntimeError, "successfully completed experiment"):
            self.runner.cleaning_electrochemical_cell()
        self.runner.opc.write_value.assert_not_called()

    def test_additional_cleaning_blocks_unknown_or_mismatched_routes(self):
        self.prepare_and_stop()
        self.runner.running_protocol_module = protocol
        self.runner._mb_selected_cell_port = None
        with self.assertRaisesRegex(RuntimeError, "No verified MB cell route"):
            self.runner.cleaning_electrochemical_cell()
        self.runner.opc.write_value.assert_not_called()
        self.runner._mb_selected_cell_port = 3
        self.runner.cell_valve.position = 1
        with self.assertRaisesRegex(RuntimeError, "AMF cell outlet port 3"):
            self.runner.cleaning_electrochemical_cell()
        self.assertFalse(any(args.args[0] == protocol.PUMP_CLEANING_TAG and args.args[1] > 0
                             for args in self.runner.opc.write_value.call_args_list))

    def test_generic_maintenance_dispatches_optional_protocol_hook(self):
        hook = Mock()
        self.runner.running_protocol_module = type("Protocol", (), {"cleaning_electrochemical_cell": hook})
        self.runner.cleaning_electrochemical_cell()
        hook.assert_called_once_with(self.runner)
        self.runner.opc.write_value.assert_not_called()

    def test_generic_maintenance_without_hook_keeps_legacy_path(self):
        self.runner.running_protocol_module = None
        self.runner.cleaning_electrochemical_cell()
        self.assertIn(call(protocol.CELL_INLET_VALVE_TAG, 5), self.runner.opc.write_value.call_args_list)

    def test_full_simulation_does_not_invoke_maintenance_hook(self):
        hook = Mock()
        self.runner.running_protocol_module = type("Protocol", (), {"cleaning_electrochemical_cell": hook})
        self.runner.simulation_mode = "full"
        self.runner.cleaning_electrochemical_cell()
        hook.assert_not_called()
        self.runner.opc.write_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
