"""Connection lifecycle checks; every device is an in-memory fake."""

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.hardware.AMF_hardware.multiport_valve import MultiportValve
from core.hardware.AMF_hardware.spm_pump import SPMPump
from core.hardware.connections import campaign_connections, close_runner
from core.hardware.experimental_run import ExperimentRunner, replace_experiment_runner


class NativeConnection:
    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.connected = True
        self.fail = None
        self.silent_failure = False

    def disconnect(self):
        self.events.append(f"disconnect {self.name}")
        if self.fail:
            raise self.fail
        if not self.silent_failure:
            self.connected = False


def devices(events):
    valve = MultiportValve.__new__(MultiportValve)
    valve.device = object()
    valve.valve = NativeConnection("valve", events)
    pump = SPMPump.__new__(SPMPump)
    pump.device = object()
    pump.pump = NativeConnection("pump", events)
    return valve, pump


def make_runner(events):
    valve, pump = devices(events)
    valve_native, pump_native = valve.valve, pump.pump
    with patch("core.hardware.experimental_run.MultiportValve", return_value=valve), patch(
        "core.hardware.experimental_run.SPMPump", return_value=pump
    ), patch("core.hardware.experimental_run.st.empty"), patch("core.hardware.experimental_run.st.sidebar.empty"):
        runner = ExperimentRunner(Mock(), "", simulation_mode="hybrid")
    runner.protocol_prepare_fn = Mock(side_effect=lambda *_: events.append("prepare"))
    runner.collect_measurements = Mock(return_value=2.0)
    runner.protocol_calculate_fn = Mock(return_value={"objective": 2.0})
    runner.protocol_stop_pumps_fn = Mock(side_effect=lambda *_: events.append("stop"))
    runner.protocol_cleanup_fn = Mock(side_effect=lambda *_: events.append("cleanup"))
    return runner, valve_native, pump_native


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def run_one(self, runner):
        return runner.run_experiment({}, objectives=["objective"])

    def assert_released(self, runner, valve, pump):
        self.assertTrue(runner.closed)
        self.assertFalse(valve.connected)
        self.assertFalse(pump.connected)
        self.assertIsNone(runner.cell_valve)
        self.assertIsNone(runner.spm)

    def test_prepare_failure_stops_and_cleans_before_releasing_both_devices(self):
        runner, valve, pump = make_runner(self.events)
        runner.protocol_prepare_fn.side_effect = ValueError("original preparation failure")
        with self.assertRaisesRegex(ValueError, "original preparation"):
            self.run_one(runner)
        self.assertEqual(self.events, ["stop", "cleanup", "disconnect pump", "disconnect valve"])
        self.assert_released(runner, valve, pump)

    def test_measurement_error_disconnects_after_shutdown(self):
        runner, valve, pump = make_runner(self.events)
        runner.collect_measurements.side_effect = TimeoutError("measurement timeout")
        with self.assertRaisesRegex(TimeoutError, "measurement timeout"):
            self.run_one(runner)
        self.assert_released(runner, valve, pump)
        self.assertLess(self.events.index("cleanup"), self.events.index("disconnect pump"))

    def test_preflight_error_releases_devices_before_any_protocol_execution(self):
        runner, valve, pump = make_runner(self.events)
        runner.measurement_backend = "uvvis"
        runner.simulation_mode = "off"
        runner._get_uvvis_measurement = Mock(side_effect=ValueError("invalid calibration"))
        with self.assertRaisesRegex(ValueError, "invalid calibration"):
            self.run_one(runner)
        runner.protocol_prepare_fn.assert_not_called()
        runner.protocol_stop_pumps_fn.assert_not_called()
        self.assert_released(runner, valve, pump)

    def test_display_error_also_releases_connected_devices(self):
        runner, valve, pump = make_runner(self.events)
        runner.display_experiment_info = Mock(side_effect=RuntimeError("display error"))
        with self.assertRaisesRegex(RuntimeError, "display error"):
            runner.run_experiment({}, experiment_number=1, total_iterations=2)
        self.assert_released(runner, valve, pump)

    def test_cleanup_failure_releases_connections_and_preserves_experiment_error(self):
        runner, valve, pump = make_runner(self.events)
        runner.collect_measurements.side_effect = ValueError("measurement error")
        runner.protocol_cleanup_fn.side_effect = OSError("cleanup error")
        with self.assertRaisesRegex(ValueError, "measurement error"):
            self.run_one(runner)
        self.assert_released(runner, valve, pump)

    def test_cleanup_failure_after_valid_result_disconnects_and_rejects_result(self):
        runner, valve, pump = make_runner(self.events)
        runner.protocol_cleanup_fn.side_effect = OSError("cleanup error")
        with self.assertRaisesRegex(OSError, "cleanup error"):
            self.run_one(runner)
        self.assert_released(runner, valve, pump)

    def test_interrupted_experiment_releases_connections(self):
        class PageInterrupted(BaseException):
            pass

        runner, valve, pump = make_runner(self.events)
        runner.collect_measurements.side_effect = PageInterrupted()
        with self.assertRaises(PageInterrupted):
            self.run_one(runner)
        self.assert_released(runner, valve, pump)

    def test_success_keeps_connections_and_contents_between_experiments(self):
        runner, valve, pump = make_runner(self.events)
        identity = ("NaCl", 0.4, 2)
        runner._mb_spm_contents = identity
        self.assertEqual(self.run_one(runner), {"objective": 2.0})
        self.assertEqual(self.run_one(runner), {"objective": 2.0})
        self.assertFalse(runner.closed)
        self.assertTrue(valve.connected and pump.connected)
        self.assertEqual(runner._mb_spm_contents, identity)
        self.assertFalse(any(e.startswith("disconnect") for e in self.events))

    def test_close_is_idempotent_and_invalidates_syringe_identity(self):
        runner, valve, pump = make_runner(self.events)
        runner._mb_spm_contents = ("NaCl", 0.4, 2)
        runner.close()
        runner.close()
        close_runner(runner)
        self.assertEqual(self.events, ["disconnect pump", "disconnect valve"])
        self.assertIsNone(runner._mb_spm_contents)
        self.assert_released(runner, valve, pump)

    def test_closed_runner_cannot_execute_another_experiment(self):
        runner, _, _ = make_runner(self.events)
        runner.close()
        with self.assertRaisesRegex(RuntimeError, "Start or resume"):
            self.run_one(runner)
        runner.protocol_prepare_fn.assert_not_called()

    def test_one_failed_disconnect_does_not_skip_the_other_device(self):
        runner, valve, pump = make_runner(self.events)
        pump.fail = OSError("USB close failed")
        with self.assertRaisesRegex(RuntimeError, "USB close failed"):
            runner.close()
        self.assertFalse(valve.connected)
        self.assertTrue(pump.connected)
        self.assertIsNotNone(runner.spm)
        self.assertIsNone(runner.cell_valve)
        pump.fail = None
        runner.close()
        self.assertEqual(self.events.count("disconnect valve"), 1)
        self.assert_released(runner, valve, pump)

    def test_disconnect_failure_does_not_replace_original_experiment_exception(self):
        runner, valve, pump = make_runner(self.events)
        pump.fail = OSError("USB close failed")
        runner.collect_measurements.side_effect = ValueError("original failure")
        with self.assertRaisesRegex(ValueError, "original failure"):
            self.run_one(runner)
        self.assertFalse(valve.connected)
        self.assertIn("disconnection also failed", self.output.getvalue())

    def test_native_sdk_silent_close_failure_is_detected(self):
        runner, valve, pump = make_runner(self.events)
        pump.silent_failure = True
        with self.assertRaisesRegex(RuntimeError, "still open"):
            runner.close()
        self.assertTrue(pump.connected)
        self.assertFalse(valve.connected)

    def test_replacement_closes_old_connections_before_new_discovery(self):
        old, old_valve, old_pump = make_runner(self.events)
        new_valve, new_pump = devices(self.events)

        def discover(**kwargs):
            self.assertFalse(old_valve.connected or old_pump.connected)
            self.events.append("discover new valve")
            return new_valve

        with patch("core.hardware.experimental_run.MultiportValve", side_effect=discover), patch(
            "core.hardware.experimental_run.SPMPump", return_value=new_pump
        ), patch("core.hardware.experimental_run.st.empty"), patch("core.hardware.experimental_run.st.sidebar.empty"):
            replacement = replace_experiment_runner(old, Mock(), "", simulation_mode="hybrid")
        self.assertEqual(self.events[:3], ["disconnect pump", "disconnect valve", "discover new valve"])
        self.assertFalse(replacement.closed)

    def test_failed_disconnection_blocks_replacement_instead_of_rescanning_busy_ports(self):
        runner, _, pump = make_runner(self.events)
        pump.fail = OSError("close failed")
        with patch("core.hardware.experimental_run.MultiportValve") as discovery:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                replace_experiment_runner(runner, Mock(), "", simulation_mode="hybrid")
        discovery.assert_not_called()

    def test_failed_pump_initialization_releases_the_open_valve(self):
        valve, _ = devices(self.events)
        native = valve.valve
        with patch("core.hardware.experimental_run.MultiportValve", return_value=valve), patch(
            "core.hardware.experimental_run.SPMPump", side_effect=RuntimeError("pump initialization failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "pump initialization failed"):
                ExperimentRunner(Mock(), "", simulation_mode="hybrid")
        self.assertFalse(native.connected)
        self.assertEqual(self.events, ["disconnect valve"])

    def test_configuration_failure_after_device_creation_releases_both(self):
        valve, pump = devices(self.events)
        valve_native, pump_native = valve.valve, pump.pump
        with patch("core.hardware.experimental_run.MultiportValve", return_value=valve), patch(
            "core.hardware.experimental_run.SPMPump", return_value=pump
        ), patch("core.hardware.experimental_run.create_process_adapter", side_effect=ValueError("bad adapter")):
            with self.assertRaisesRegex(ValueError, "bad adapter"):
                ExperimentRunner(Mock(), "", simulation_mode="hybrid")
        self.assertFalse(valve_native.connected or pump_native.connected)

    def test_device_wrapper_releases_handle_if_connect_or_home_raises(self):
        for wrapper_class, handle in ((MultiportValve, "valve"), (SPMPump, "pump")):
            for phase in ("connect", "home"):
                with self.subTest(wrapper=wrapper_class.__name__, phase=phase):
                    native = NativeConnection(handle, self.events)

                    def connect(obj):
                        setattr(obj, handle, native)
                        if phase == "connect":
                            raise OSError("initialization failure")

                    with patch.object(wrapper_class, "connect", connect), patch.object(
                        wrapper_class, "ensure_homed", side_effect=OSError("homing failure")
                    ):
                        with self.assertRaises(OSError):
                            wrapper_class()
                    self.assertFalse(native.connected)

    def test_legacy_session_runner_without_close_still_releases_devices(self):
        valve, pump = devices(self.events)
        valve_native, pump_native = valve.valve, pump.pump
        legacy = SimpleNamespace(cell_valve=valve, spm=pump)
        close_runner(legacy)
        close_runner(legacy)
        self.assertFalse(valve_native.connected or pump_native.connected)
        self.assertTrue(legacy._closed)
        self.assertEqual(self.events, ["disconnect pump", "disconnect valve"])

    def test_campaign_context_keeps_connections_between_runs_and_closes_on_completion(self):
        runner, valve, pump = make_runner(self.events)
        with campaign_connections(runner):
            self.run_one(runner)
            self.assertTrue(pump.connected and valve.connected)
            self.run_one(runner)
        self.assert_released(runner, valve, pump)

    def test_error_outside_run_experiment_releases_campaign_connections(self):
        runner, valve, pump = make_runner(self.events)
        with self.assertRaisesRegex(OSError, "saving results failed"):
            with campaign_connections(runner):
                self.run_one(runner)
                raise OSError("saving results failed")
        self.assert_released(runner, valve, pump)

    def test_campaign_context_preserves_error_if_disconnect_also_fails(self):
        runner, valve, pump = make_runner(self.events)
        pump.fail = OSError("USB close failed")
        with self.assertRaisesRegex(ValueError, "optimizer update failed"):
            with campaign_connections(runner):
                raise ValueError("optimizer update failed")
        self.assertFalse(valve.connected)


if __name__ == "__main__":
    unittest.main()
