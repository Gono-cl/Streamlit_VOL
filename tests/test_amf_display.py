"""Exercise USB commands and OPC display updates together, without hardware."""

import contextlib
import io
import unittest
from unittest.mock import Mock, call, patch

import requests

from core.hardware.experimental_run import (
    AMF_DISPLAY_TIMEOUT_S, CELL_MULTIPORT_TAG, SPM_FLOW_TAG, SPM_INTERNAL_VALVE_TAG,
)
from core.hardware.opc_communication import OPCClient
from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


class AMFDisplayTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def record_writes(self, runner, sdk):
        def record(tag, value, **kwargs):
            self.assertEqual(kwargs, {"timeout": AMF_DISPLAY_TIMEOUT_S})
            sdk.events.append(("opc", tag, value))
            return True
        runner.opc.write_value.side_effect = record

    def test_cell_display_uses_reported_position_after_the_usb_move(self):
        runner, sdk = make_runner()
        self.record_writes(runner, sdk)

        def move(port):
            sdk.events.append(("cell_port", 2))
            return 2  # Reported position differs from the requested port.
        runner.cell_valve.move_to.side_effect = move
        self.assertEqual(runner.move_cell_valve(3), 2)
        self.assertEqual(sdk.events, [("cell_port", 2), ("opc", CELL_MULTIPORT_TAG, 2)])

    def test_internal_valve_display_is_written_after_position_verification(self):
        runner, sdk = make_runner()
        self.record_writes(runner, sdk)
        runner.move_spm_valve(5)
        self.assertEqual(sdk.events, [("port", 5), ("opc", SPM_INTERNAL_VALVE_TAG, 5)])

    def test_failed_valve_moves_do_not_publish_the_requested_position(self):
        runner, sdk = make_runner()
        sdk.getValvePosition = lambda: 3
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            runner.move_spm_valve(2)
        runner.opc.write_value.assert_not_called()
        runner.cell_valve.move_to.side_effect = OSError("USB connection lost")
        with self.assertRaises(OSError):
            runner.move_cell_valve(2)
        runner.opc.write_value.assert_not_called()

    def test_homing_reports_verified_port_one_and_zero_flow(self):
        runner, sdk = make_runner(5000, initialized=False)
        sdk.fault = 145
        self.record_writes(runner, sdk)
        runner.home_spm_to_waste(waste_port=1)
        self.assertEqual(sdk.events[:2], [("stop",), ("home", 1, False)])
        self.assertEqual(sdk.events[-2:], [("opc", SPM_INTERNAL_VALVE_TAG, 1), ("opc", SPM_FLOW_TAG, 0.0)])
        self.assertEqual(sdk.volume, 0)

    def test_configuring_speed_leaves_zero_displayed_until_delivery_starts(self):
        runner, sdk = make_runner(5000)
        sdk.getFlowRate = lambda: 232.666  # Device rounds the requested speed.
        self.assertEqual(runner.set_spm_flow(232.7), 232.666)
        self.assertEqual(runner.opc.write_value.call_args, call(SPM_FLOW_TAG, 0.0, timeout=AMF_DISPLAY_TIMEOUT_S))
        runner.move_spm_to_volume(0, block=False)
        self.assertIsNotNone(sdk.pending)
        self.assertEqual(runner.opc.write_value.call_args, call(SPM_FLOW_TAG, 232.666, timeout=AMF_DISPLAY_TIMEOUT_S))
        sdk.consume(1000)
        state = runner.stop_spm()
        self.assertAlmostEqual(state["volume_uL"], 4000)
        self.assertEqual(runner.opc.write_value.call_args, call(SPM_FLOW_TAG, 0.0, timeout=AMF_DISPLAY_TIMEOUT_S))

    def test_blocking_fill_displays_motion_after_acceptance_and_zero_after_completion(self):
        runner, sdk = make_runner()
        runner.move_spm_valve(2)
        runner.set_spm_flow(5000)
        sdk.events.clear()

        def record(tag, value, **kwargs):
            if value:
                self.assertIsNotNone(sdk.pending)
                self.assertEqual(sdk.volume, 0)  # Encoder has not completed the fill.
            else:
                self.assertEqual(sdk.volume, 5000)
            sdk.events.append(("opc", tag, value))
        runner.opc.write_value.side_effect = record
        runner.move_spm_to_volume(5000)
        self.assertEqual(sdk.events, [
            ("volume", 2, 5000.0), ("opc", SPM_FLOW_TAG, 5000.0), ("opc", SPM_FLOW_TAG, 0.0),
        ])

    def test_already_full_syringe_does_not_display_a_fictitious_fill_flow(self):
        runner, sdk = make_runner(5000)
        runner.move_spm_valve(2)
        runner.set_spm_flow(5000)
        runner.opc.write_value.reset_mock()
        runner.move_spm_to_volume(5000)
        self.assertTrue(all(args.args[1] == 0 for args in runner.opc.write_value.call_args_list))

    def test_command_rejection_does_not_publish_a_running_flow(self):
        runner, sdk = make_runner(5000)
        runner.set_spm_flow(500)
        sdk.fail_volume = 0
        runner.opc.write_value.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "range"):
            runner.move_spm_to_volume(0, block=False)
        runner.opc.write_value.assert_not_called()

    def test_uncertain_stop_does_not_publish_a_false_zero(self):
        runner, sdk = make_runner(5000)
        runner.set_spm_flow(500)
        runner.move_spm_to_volume(0, block=False)
        runner.opc.write_value.reset_mock()
        sdk.fault = 145
        with self.assertRaisesRegex(RuntimeError, "145"):
            runner.stop_spm()
        runner.opc.write_value.assert_not_called()
        self.assertTrue(runner._spm_motion_active)

    def test_display_failure_does_not_interrupt_filling_or_usb_stop(self):
        runner, sdk = make_runner()
        runner.opc.write_value.side_effect = OSError("Display server unavailable")
        runner.move_spm_valve(2)
        runner.set_spm_flow(5000)
        self.assertEqual(runner.move_spm_to_volume(5000)["volume_uL"], 5000)
        runner.move_spm_valve(4)
        runner.set_spm_flow(300)
        runner.move_spm_to_volume(0, block=False)
        sdk.consume(500)
        self.assertEqual(runner.stop_spm()["volume_uL"], 4500)
        self.assertIsNone(sdk.pending)
        self.assertIn("AMF display update failed", self.output.getvalue())

    def test_mb_preparation_delivery_and_shutdown_publish_all_three_tags(self):
        runner, sdk = make_runner(5000, initialized=False)
        params = {"current_density": 3.028, "applied_charge": 6.753,
                  "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
                  "cell": "EC3", "electrolyte": "Na2SO4"}
        with patch.object(protocol, "_log_operating_point"), patch.object(protocol.time, "sleep"):
            op = protocol.prepare_hardware(runner, params)
        writes = list(runner.opc.write_value.call_args_list)
        self.assertIn(call(CELL_MULTIPORT_TAG, 3, timeout=AMF_DISPLAY_TIMEOUT_S), writes)
        self.assertIn(call(SPM_INTERNAL_VALVE_TAG, 1, timeout=AMF_DISPLAY_TIMEOUT_S), writes)
        self.assertIn(call(SPM_INTERNAL_VALVE_TAG, 5, timeout=AMF_DISPLAY_TIMEOUT_S), writes)
        self.assertIn(call(SPM_INTERNAL_VALVE_TAG, 4, timeout=AMF_DISPLAY_TIMEOUT_S), writes)
        flow_writes = [args.args[1] for args in writes if args.args[0] == SPM_FLOW_TAG]
        self.assertIn(5000.0, flow_writes)  # Rinsing/filling motion.
        self.assertAlmostEqual(flow_writes[-1], op["flow_electrolyte_uL_min"])
        protocol.stop_pumps(runner, params)
        self.assertIn(call(SPM_FLOW_TAG, 0.0, timeout=AMF_DISPLAY_TIMEOUT_S), runner.opc.write_value.call_args_list[len(writes):])

    def test_opc_display_timeout_is_bounded_and_legacy_writes_still_work(self):
        opc = OPCClient("http://example.invalid")
        response = Mock()
        with patch("core.hardware.opc_communication.requests.get", return_value=response) as get:
            self.assertTrue(opc.write_value(SPM_FLOW_TAG, 232.7, timeout=AMF_DISPLAY_TIMEOUT_S))
            self.assertEqual(get.call_args.kwargs, {"timeout": AMF_DISPLAY_TIMEOUT_S})
            self.assertIn("value=232,7", get.call_args.args[0])
            self.assertTrue(opc.write_value("legacy-tag", 1))
            self.assertEqual(get.call_args.kwargs, {})
        with patch("core.hardware.opc_communication.requests.get", side_effect=requests.Timeout("Timed out")):
            self.assertFalse(opc.write_value(SPM_FLOW_TAG, 0, timeout=AMF_DISPLAY_TIMEOUT_S))


if __name__ == "__main__":
    unittest.main()
