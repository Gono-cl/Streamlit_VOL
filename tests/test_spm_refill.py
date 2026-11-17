"""Exercise syringe refills and MB changeovers with a simulated AMF device."""

import contextlib
import io
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from core.hardware.AMF_hardware.spm_pump import SPMPump
from running_protocols import MB_6_variables as protocol


class FakeAMF:
    """Model encoder position and valve routing; never open a serial device."""

    def __init__(self, volume=0.0, resolution=0):
        self.resolution = resolution
        self.full_stroke = 3000 if resolution == 0 else 24000
        self.actual_steps = volume / 5000.0 * self.full_stroke
        self.commanded_steps = self.actual_steps
        self.valve = 4
        self.flow = 5000.0
        self.pending = None
        self.fault = 0
        self.homed = True
        self.never_complete = False
        self.stop_error = None
        self.fail_volume = None
        self.moves = []
        self.events = []

    @property
    def volume(self):
        return self.actual_steps / self.full_stroke * 5000.0

    def getPumpStatus(self):
        if self.fault:
            return self.fault
        if self.pending is not None:
            if self.never_complete:
                return 255
            self.actual_steps = self.pending
            self.pending = None
        return 0

    def getHomeStatus(self):
        return self.homed

    def getMicrostepResolution(self):
        return self.resolution

    def getRealPlungerPosition(self):
        return self.actual_steps

    def getPlungerPosition(self):
        return self.commanded_steps

    def getPortNumber(self):
        return 6

    def getValvePosition(self):
        return self.valve

    def valveShortestPath(self, port):
        if self.pending is not None:
            raise AssertionError("Valve moved while the syringe was dispensing")
        self.valve = port
        self.events.append(("port", port))

    def setFlowRate(self, flowRate, syringeVolume):
        self.flow = flowRate
        self.events.append(("flow", flowRate))

    def getFlowRate(self):
        return self.flow

    def pumpVolume(self, volume, syringeVolume, block):
        if self.pending is not None:
            raise AssertionError("Overlapping syringe movements")
        if volume == self.fail_volume:
            self.fault = 145
            raise RuntimeError("Move out of range")
        if not 0 <= volume <= syringeVolume:
            raise AssertionError("Syringe capacity exceeded")
        delta = volume - self.volume
        if self.valve in (1, 2, 3) and delta < -0.01:
            raise AssertionError("Residual solution returned to stock")
        if self.valve == 5 and delta > 0.01:
            raise AssertionError("Waste aspirated into syringe")
        self.moves.append((self.valve, volume, round(delta, 6), block))
        self.events.append(("volume", self.valve, volume))
        self.commanded_steps = volume / syringeVolume * self.full_stroke
        self.pending = self.commanded_steps

    def hardStop(self):
        self.events.append(("stop",))
        if self.stop_error:
            raise self.stop_error
        self.pending = None

    def setPumpStrengthAndHome(self, **kwargs):
        raise AssertionError("Routine refill must not home the syringe")

    def consume(self, volume):
        if self.pending is None or self.valve != 4:
            raise AssertionError("Delivery has not started")
        self.actual_steps -= volume / 5000.0 * self.full_stroke


def make_pump(volume=0.0, resolution=0):
    sdk = FakeAMF(volume, resolution)
    pump = SPMPump.__new__(SPMPump)
    pump.pump = sdk
    pump.syringe_volume = 5000
    return pump, sdk


def make_runner(volume=0.0, resolution=0):
    pump, sdk = make_pump(volume, resolution)
    runner = SimpleNamespace(
        spm=pump, simulation_mode="hybrid", adapter_config={},
        opc=Mock(), cell_valve=Mock(), turn_off_power_supply=Mock(),
        turn_on_power_supply=Mock(), set_current=Mock(),
    )
    return runner, sdk


def operating(electrolyte="NaCl", stock=0.4):
    return {
        "electrolyte": electrolyte,
        "electrolyte_stock_M": stock,
        "electrolyte_spm_port": protocol.ELECTROLYTE_METADATA[electrolyte]["spm_port"],
        "flow_electrolyte_uL_min": 232.7,
    }


def identity(op):
    return (op["electrolyte"], op["electrolyte_stock_M"], op["electrolyte_spm_port"])


class QuietTest(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)


class SyringeDriverTests(QuietTest):
    def test_encoder_position_and_resolution_determine_remaining_volume(self):
        for resolution in (0, 1):
            with self.subTest(resolution=resolution):
                pump, sdk = make_pump(3200, resolution)
                sdk.commanded_steps = 0  # A previous dispense target is not the contents.
                state = pump.get_syringe_state()
                self.assertAlmostEqual(state["volume_uL"], 3200)
                self.assertEqual(state["commanded_steps"], 0)

    def test_filling_to_target_adds_only_missing_volume(self):
        pump, sdk = make_pump(3200)
        pump.move_valve(2)
        pump.set_flow_rate(5000)
        result = pump.move_to_volume(5000)
        self.assertEqual(sdk.moves, [(2, 5000, 1800, False)])
        self.assertEqual(result["volume_uL"], 5000)

    def test_nonblocking_delivery_retains_actual_contents_until_stopped(self):
        pump, sdk = make_pump(5000)
        pump.move_to_volume(0, block=False)
        self.assertIsNotNone(sdk.pending)
        sdk.consume(1800)
        result = pump.hard_stop()
        self.assertAlmostEqual(result["volume_uL"], 3200)
        self.assertEqual(result["commanded_steps"], 0)
        self.assertIsNone(sdk.pending)

    def test_invalid_volumes_do_not_send_motion_commands(self):
        for target in (-1, 5001, math.nan, math.inf):
            with self.subTest(target=target):
                pump, sdk = make_pump()
                with self.assertRaises(ValueError):
                    pump.move_to_volume(target)
                self.assertEqual(sdk.moves, [])

    def test_busy_or_faulted_pump_blocks_refill(self):
        for status in (255, 145, 144, None):
            with self.subTest(status=status):
                pump, sdk = make_pump()
                sdk.getPumpStatus = lambda: status
                with self.assertRaises(RuntimeError):
                    pump.move_to_volume(5000)
                self.assertEqual(sdk.moves, [])

    def test_unhomed_and_invalid_encoder_states_block_motion(self):
        pump, sdk = make_pump()
        sdk.homed = False
        with self.assertRaisesRegex(RuntimeError, "not homed"):
            pump.move_to_volume(5000)
        sdk.homed = True
        for position in (-1, 3001, math.nan, math.inf):
            with self.subTest(position=position):
                sdk.actual_steps = position
                with self.assertRaises(RuntimeError):
                    pump.move_to_volume(5000)
        self.assertEqual(sdk.moves, [])

    def test_unknown_resolution_blocks_motion(self):
        pump, sdk = make_pump()
        sdk.resolution = 7
        with self.assertRaisesRegex(RuntimeError, "resolution"):
            pump.move_to_volume(5000)
        self.assertEqual(sdk.moves, [])

    def test_bad_waste_port_is_rejected_before_valve_command(self):
        pump, sdk = make_pump()
        for port in (0, 7, True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                pump.move_valve(port)
        self.assertEqual(sdk.events, [])

    def test_unconfirmed_valve_position_blocks_refill(self):
        pump, sdk = make_pump()
        sdk.getValvePosition = lambda: 3
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            pump.move_valve(5)

    def test_move_timeout_does_not_start_another_movement(self):
        pump, sdk = make_pump(1000)
        sdk.valve = 2
        sdk.never_complete = True
        with patch("core.hardware.AMF_hardware.spm_pump.time.monotonic", side_effect=[0, 1000]):
            with self.assertRaises(TimeoutError):
                pump.move_to_volume(5000)
        self.assertEqual(len(sdk.moves), 1)

    def test_encoder_must_confirm_motion_completed(self):
        pump, sdk = make_pump(1000)
        sdk.valve = 2
        sdk.getPumpStatus = lambda: 0  # Device claims ready but never moves.
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            pump.move_to_volume(5000)

    def test_hard_stop_cannot_report_success_with_fault_145(self):
        pump, sdk = make_pump(5000)
        sdk.fault = 145
        with self.assertRaisesRegex(RuntimeError, "145"):
            pump.hard_stop()
        self.assertNotIn("ready", self.output.getvalue())


class ElectrolyteRefillTests(QuietTest):
    def test_runner_does_not_home_the_syringe_before_protocol_state_checks(self):
        from core.hardware.experimental_run import ExperimentRunner

        with patch("core.hardware.experimental_run.MultiportValve"), patch(
            "core.hardware.experimental_run.SPMPump"
        ) as spm_class, patch("core.hardware.experimental_run.st.empty"), patch(
            "core.hardware.experimental_run.st.sidebar.empty"
        ):
            ExperimentRunner(Mock(), "", simulation_mode="off")
        spm_class.assert_called_once_with(auto_home=False, syringe_volume=5000)

    def test_same_known_stock_tops_up_without_discard_or_rinse(self):
        runner, sdk = make_runner(3200)
        op = operating()
        runner._mb_spm_contents = identity(op)
        protocol._prepare_spm_electrolyte(runner, op)
        self.assertEqual(sdk.moves, [(2, 5000, 1800, False)])
        self.assertNotIn(("port", 5), sdk.events)
        self.assertEqual(runner._mb_spm_contents, identity(op))
        self.assertEqual(sdk.valve, 4)
        self.assertEqual(sdk.flow, op["flow_electrolyte_uL_min"])

    def test_same_stock_with_different_reaction_concentration_only_changes_flow(self):
        runner, sdk = make_runner(4000)
        op = operating()
        runner._mb_spm_contents = identity(op)
        op["flow_electrolyte_uL_min"] = 300.0
        protocol._prepare_spm_electrolyte(runner, op)
        self.assertEqual(sdk.moves, [(2, 5000, 1000, False)])
        self.assertEqual(sdk.flow, 300)

    def test_full_known_syringe_is_not_overfilled(self):
        runner, sdk = make_runner(5000)
        op = operating()
        runner._mb_spm_contents = identity(op)
        protocol._prepare_spm_electrolyte(runner, op)
        self.assertEqual(sdk.moves, [(2, 5000, 0, False)])

    def test_changed_electrolyte_discards_then_rinses_twice_with_incoming_stock(self):
        runner, sdk = make_runner(3200)
        runner._mb_spm_contents = identity(operating("Na2SO4"))
        protocol._prepare_spm_electrolyte(runner, operating("NaCl"))
        self.assertEqual(sdk.moves, [
            (5, 0, -3200, False),
            (2, 1000, 1000, False), (5, 0, -1000, False),
            (2, 1000, 1000, False), (5, 0, -1000, False),
            (2, 5000, 5000, False),
        ])

    def test_unknown_contents_after_restart_are_not_assumed_to_match(self):
        for remaining in (0, 3200):
            with self.subTest(remaining=remaining):
                runner, sdk = make_runner(remaining)
                protocol._prepare_spm_electrolyte(runner, operating())
                self.assertEqual(sdk.moves[0][0:2], (5, 0))
                self.assertEqual([v for port, v, _, _ in sdk.moves if port == 2], [1000, 1000, 5000])

    def test_stock_concentration_and_source_port_are_part_of_identity(self):
        for previous in (("NaCl", 0.5, 2), ("NaCl", 0.4, 3)):
            with self.subTest(previous=previous):
                runner, sdk = make_runner(3200)
                runner._mb_spm_contents = previous
                protocol._prepare_spm_electrolyte(runner, operating())
                self.assertEqual(sdk.moves[0][0:2], (5, 0))

    def test_rinse_configuration_can_be_overridden(self):
        runner, sdk = make_runner(2000)
        runner.adapter_config = {"spm_waste_port": 6, "spm_rinse_volume_uL": 500, "spm_rinse_cycles": 1}
        protocol._prepare_spm_electrolyte(runner, operating())
        self.assertEqual([(port, volume) for port, volume, _, _ in sdk.moves],
                         [(6, 0), (2, 500), (6, 0), (2, 5000)])

    def test_invalid_configuration_blocks_all_fluid_movements(self):
        for config in (
            {"spm_waste_port": 2}, {"spm_waste_port": 4},
            {"spm_waste_port": True}, {"spm_waste_port": 7},
            {"spm_rinse_cycles": 0}, {"spm_rinse_cycles": 1.5},
            {"spm_rinse_volume_uL": 0}, {"spm_rinse_volume_uL": 6000},
            {"spm_rinse_volume_uL": math.nan},
        ):
            with self.subTest(config=config):
                runner, sdk = make_runner(3200)
                runner.adapter_config = config
                with self.assertRaises(ValueError):
                    protocol._prepare_spm_electrolyte(runner, operating())
                self.assertEqual(sdk.moves, [])
                self.assertIsNone(runner._mb_spm_contents)

    def test_rinse_failure_invalidates_contents_and_blocks_later_refill(self):
        runner, sdk = make_runner(3200)
        sdk.fail_volume = 1000
        with self.assertRaisesRegex(RuntimeError, "range"):
            protocol._prepare_spm_electrolyte(runner, operating())
        self.assertIsNone(runner._mb_spm_contents)
        before = list(sdk.moves)
        with self.assertRaisesRegex(RuntimeError, "145"):
            protocol._prepare_spm_electrolyte(runner, operating())
        self.assertEqual(sdk.moves, before)

    def test_same_stock_delivery_stop_and_next_refill_form_two_experiments(self):
        runner, sdk = make_runner()
        op = operating()
        protocol._prepare_spm_electrolyte(runner, op)
        protocol._start_spm_delivery(runner)
        self.assertTrue(runner._mb_spm_delivery_active)
        self.assertEqual(sdk.moves[-1][0:2], (4, 0))
        sdk.consume(1800)
        protocol.stop_pumps(runner, {})
        protocol.cleanup(runner, {})
        self.assertFalse(runner._mb_spm_delivery_active)
        self.assertEqual(runner._mb_spm_contents, identity(op))
        self.assertAlmostEqual(sdk.volume, 3200)
        before = len(sdk.moves)
        protocol._prepare_spm_electrolyte(runner, op)
        self.assertEqual(sdk.moves[before:], [(2, 5000, 1800, False)])

    def test_two_complete_preparations_use_the_new_refill_path(self):
        runner, sdk = make_runner()
        params = {
            "current_density": 3.028, "applied_charge": 6.753,
            "substrate_concentration": 309.39, "electrolyte_concentration": 0.0666,
            "cell": "EC3", "electrolyte": "NaCl",
        }
        with patch.object(protocol, "_log_operating_point"), patch.object(protocol.time, "sleep"):
            protocol.prepare_hardware(runner, params)
            sdk.consume(1800)
            protocol.stop_pumps(runner, params)
            before = len(sdk.moves)
            params["electrolyte_concentration"] = 0.07
            protocol.prepare_hardware(runner, params)
            self.assertEqual(sdk.moves[before:], [(2, 5000, 1800, False), (4, 0, -5000, False)])
            protocol.stop_pumps(runner, params)
        self.assertEqual(runner._mb_spm_contents, ("NaCl", 0.4, 2))
        runner.turn_on_power_supply.assert_has_calls([call(), call()])

    def test_preparation_error_still_shuts_down_when_error_logging_fails(self):
        runner, sdk = make_runner()
        sdk.fail_volume = 1000
        params = {
            "current_density": 1, "applied_charge": 10,
            "substrate_concentration": 100, "electrolyte_concentration": 0.05,
            "cell": "EC1", "electrolyte": "NaCl",
        }
        with patch.object(protocol, "_log_operating_point", side_effect=OSError("Log path unavailable")):
            with self.assertRaisesRegex(RuntimeError, "range"):
                protocol.prepare_hardware(runner, params)
        self.assertIsNone(runner._mb_spm_contents)
        runner.opc.write_value.assert_has_calls([
            call(protocol.PUMP_MB_TAG, 0), call(protocol.PUMP_WATER_TAG, 0),
            call(protocol.PUMP_ANALYTICAL_DILUTION_TAG, 0),
        ])
        runner.turn_on_power_supply.assert_not_called()

    def test_refill_first_stops_an_active_nonblocking_delivery(self):
        runner, sdk = make_runner()
        op = operating()
        protocol._prepare_spm_electrolyte(runner, op)
        protocol._start_spm_delivery(runner)
        sdk.consume(1000)
        before = len(sdk.moves)
        protocol._prepare_spm_electrolyte(runner, op)
        self.assertEqual(sdk.moves[before:], [(2, 5000, 1000, False)])
        self.assertFalse(runner._mb_spm_delivery_active)

    def test_uncertain_stop_still_attempts_to_stop_all_other_feed_pumps(self):
        runner, sdk = make_runner(3200)
        runner._mb_spm_contents = identity(operating())
        sdk.stop_error = OSError("Serial link lost")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            protocol.stop_pumps(runner, {})
        self.assertIsNone(runner._mb_spm_contents)
        runner.turn_off_power_supply.assert_called_once()
        runner.opc.write_value.assert_has_calls([
            call(protocol.PUMP_MB_TAG, 0), call(protocol.PUMP_WATER_TAG, 0),
            call(protocol.PUMP_ANALYTICAL_DILUTION_TAG, 0),
        ])

    def test_emergency_shutdown_is_best_effort_and_invalidates_stock_identity(self):
        runner, sdk = make_runner(3200)
        runner._mb_spm_contents = identity(operating())
        sdk.fault = 145
        protocol.emergency_shutdown(runner)
        self.assertIsNone(runner._mb_spm_contents)
        self.assertIn("could not verify", self.output.getvalue())

    def test_simulation_does_not_move_or_stop_any_device(self):
        runner, sdk = make_runner(3200)
        runner.simulation_mode = "full"
        params = {
            "current_density": 1, "applied_charge": 10,
            "substrate_concentration": 100, "electrolyte_concentration": 0.05,
            "cell": "EC1", "electrolyte": "NaCl",
        }
        with patch.object(protocol, "_log_operating_point"):
            protocol.prepare_hardware(runner, params)
        protocol.stop_pumps(runner, params)
        protocol.cleanup(runner, params)
        self.assertEqual(sdk.events, [])
        runner.opc.write_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
