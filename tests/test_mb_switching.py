"""Command order and stop delays with a fake clock, OPC, and USB equipment."""
import contextlib
import io
import math
import unittest
from unittest.mock import patch

from running_protocols import MB_6_variables as protocol
from tests.test_spm_refill import make_runner


PARAMS = {'current_density': 3.028, 'applied_charge': 6.753,
          'substrate_concentration': 309.39, 'electrolyte_concentration': 0.0666,
          'cell': 'EC3', 'electrolyte': 'NaCl'}


class SwitchingTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(protocol, '_log_operating_point'))
        self.sleep = stack.enter_context(patch.object(protocol.time, 'sleep'))
        self.runner, self.sdk = make_runner(5000, initialized=False)
        self.now = 0.0
        self.events = []
        self.sleep.side_effect = self.advance
        self.original_write = self.runner.opc.write_value.side_effect
        self.runner.opc.write_value.side_effect = self.write

    def advance(self, seconds):
        self.events.append(('wait', seconds, self.now))
        self.now += seconds

    def write(self, tag, value, **kwargs):
        self.events.append(('write', tag, value, self.now))
        return self.original_write(tag, value, **kwargs)

    def flags(self):
        return tuple(self.runner.opc.values.get(tag, 0) for tag in protocol.BKK_CHANNEL_TAGS.values())

    def prepare_cleaning(self):
        protocol.prepare_hardware(self.runner, PARAMS)
        protocol.stop_pumps(self.runner, PARAMS)
        self.runner._experiment_completed = True
        self.events.clear()
        self.sleep.reset_mock()

    def test_higher_to_lower_channel_never_enables_both(self):
        self.runner.opc.values.update({tag: int(channel == 3) for channel, tag in protocol.BKK_CHANNEL_TAGS.items()})
        selected_at = []

        def write(tag, value, **kwargs):
            if tag in protocol.BKK_CHANNEL_TAGS.values():
                self.assertEqual([self.runner.opc.values[t] for t, _ in protocol.POWER_OFF_TAGS], [0, 1])
                if value == 1:
                    self.assertEqual(self.flags(), (0, 0, 0))
                    selected_at.append(self.now)
            result = self.write(tag, value, **kwargs)
            self.assertLessEqual(sum(self.flags()), 1)
            return result
        self.runner.opc.write_value.side_effect = write
        protocol._select_bkk_channel(self.runner, 1)
        self.assertEqual(self.flags(), (1, 0, 0))
        self.assertEqual(selected_at, [1.0])
        writes = [e[1:3] for e in self.events if e[0] == 'write']
        self.assertEqual(writes[:3], [(tag, 0) for tag in protocol.BKK_CHANNEL_TAGS.values()])

    def test_channel_and_matching_ports_are_selected_before_electrolyte_and_feeds(self):
        self.runner.opc.values.update({tag: 1 for tag in protocol.BKK_CHANNEL_TAGS.values()})
        original_prepare = protocol._prepare_spm_electrolyte

        def prepare(runner, op):
            self.assertEqual(self.flags(), (0, 0, 1))
            self.assertEqual(runner.cell_valve.get_position(), 3)
            self.assertEqual(runner.opc.values[protocol.CELL_INLET_VALVE_TAG], 3)
            self.assertFalse(any(e[0] == 'write' and e[1] in dict(protocol.FEED_STOP_TAGS) and e[2] > 0 for e in self.events))
            return original_prepare(runner, op)
        with patch.object(protocol, '_prepare_spm_electrolyte', side_effect=prepare) as prepared:
            protocol.prepare_hardware(self.runner, PARAMS)
        prepared.assert_called_once()

    def test_current_delay_and_final_checks_precede_power_on(self):
        powered_at = []
        def power_on():
            writes = [e for e in self.events if e[0] == 'write' and e[1] == protocol.BKK_CURRENT_SET_TAG]
            self.assertGreaterEqual(self.now - writes[-1][3], 3.0)
            self.assertEqual(self.flags(), (0, 0, 1))
            self.assertEqual(self.runner.opc.values[protocol.CELL_INLET_VALVE_TAG], 3)
            self.assertAlmostEqual(self.runner.opc.values[protocol.BKK_CURRENT_SET_TAG], 0.0147)
            powered_at.append(self.now)
        self.runner.turn_on_power_supply.side_effect = power_on
        protocol.prepare_hardware(self.runner, PARAMS)
        self.assertEqual(len(powered_at), 1)

    def test_late_second_channel_activation_blocks_power(self):
        def wait(seconds):
            self.advance(seconds)
            if seconds == 3.0:
                self.runner.opc.values[protocol.BKK_CHANNEL_TAGS[1]] = 1
        self.sleep.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, 'selected exclusively'):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()

    def test_channel_clear_write_failure_still_attempts_all_zeroes(self):
        first = protocol.BKK_CHANNEL_TAGS[1]
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: False if tag == first else self.write(tag, value, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'clear all BKK channels'):
            protocol._select_bkk_channel(self.runner, 2)
        calls = self.runner.opc.write_value.call_args_list
        self.assertTrue(all(any(c.args == (tag, 0) for c in calls) for tag in protocol.BKK_CHANNEL_TAGS.values()))
        self.assertFalse(any(c.args[1] == 1 for c in calls))

    def test_stuck_old_channel_readback_blocks_selection_and_feeds(self):
        original = self.runner.opc.read_value.side_effect
        tag = protocol.BKK_CHANNEL_TAGS[1]
        self.runner.opc.read_value.side_effect = lambda item, **kwargs: 1 if item == tag else original(item, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'all BKK channels OFF'):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()
        self.assertFalse(any(e[0] == 'write' and e[1] in dict(protocol.FEED_STOP_TAGS) and e[2] > 0 for e in self.events))
        self.assertFalse(any(e[0] == 'write' and e[1] in protocol.BKK_CHANNEL_TAGS.values() and e[2] == 1 for e in self.events))

    def test_failed_selected_channel_write_blocks_feeds_and_power(self):
        target = protocol.BKK_CHANNEL_TAGS[3]
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: False if tag == target and value == 1 else self.write(tag, value, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'Required OPC write failed'):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()
        self.assertEqual(self.flags(), (0, 0, 0))

    def test_power_must_be_off_before_direct_channel_change(self):
        self.runner.opc.values.update({tag: 1 - value for tag, value in protocol.POWER_OFF_TAGS})
        with self.assertRaisesRegex(RuntimeError, 'power OFF before BKK'):
            protocol._select_bkk_channel(self.runner, 1)
        self.runner.opc.write_value.assert_not_called()

    def test_invalid_delays_are_rejected_before_preparation(self):
        for key in ('bkk_channel_switch_delay_s', 'bkk_current_settle_s'):
            for bad in (0, -1, True, None, math.nan, math.inf):
                with self.subTest(key=key, value=bad):
                    self.runner.adapter_config = {key: bad}
                    with self.assertRaisesRegex(ValueError, 'finite and positive'):
                        protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.opc.write_value.assert_not_called()

    def test_current_write_failure_blocks_power(self):
        self.runner.opc.write_value.side_effect = lambda tag, value, **kwargs: False if tag == protocol.BKK_CURRENT_SET_TAG else self.write(tag, value, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'Required OPC write failed'):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.runner.turn_on_power_supply.assert_not_called()

    def test_incorrect_current_readback_blocks_power_after_wait(self):
        original = self.runner.opc.read_value.side_effect
        self.runner.opc.read_value.side_effect = lambda tag, **kwargs: 0 if tag == protocol.BKK_CURRENT_SET_TAG else original(tag, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'current setpoint before power ON'):
            protocol.prepare_hardware(self.runner, PARAMS)
        self.assertTrue(any(e[0:2] == ('wait', 3.0) for e in self.events))
        self.runner.turn_on_power_supply.assert_not_called()

    def test_channel_and_current_delays_can_be_configured(self):
        self.runner.adapter_config = {'bkk_channel_switch_delay_s': 2.5, 'bkk_current_settle_s': 4.5}
        op = protocol.prepare_hardware(self.runner, PARAMS)
        waits = [e[1] for e in self.events if e[0] == 'wait']
        self.assertEqual(waits[0], 2.5)
        self.assertEqual(waits[2], 4.5)
        self.assertEqual(op['bkk_current_settle_s'], 4.5)

    def test_normal_ethanol_stop_holds_route_for_five_seconds(self):
        self.prepare_cleaning()
        stopped_at = []
        valve_moves = []
        valve_tags = dict(protocol.VALVE_ELECTROLYSIS_TAGS + protocol.UVVIS_MEASUREMENT_TAGS)
        def write(tag, value, **kwargs):
            if tag == protocol.PUMP_CLEANING_TAG and value == 0 and self.runner.opc.values.get(tag, 0) > 0:
                stopped_at.append(self.now)
            if stopped_at and tag in valve_tags:
                self.assertGreaterEqual(self.now - stopped_at[-1], 5.0)
                valve_moves.append(self.now)
            return self.write(tag, value, **kwargs)
        self.runner.opc.write_value.side_effect = write
        protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(len(stopped_at), 1)
        self.assertTrue(valve_moves)
        self.assertEqual(valve_moves[0] - stopped_at[0], 5.0)
        self.assertFalse(self.runner._mb_ethanol_needs_settle)

    def test_interrupted_ethanol_stage_waits_before_restoring_valves(self):
        self.prepare_cleaning()
        def wait(seconds):
            if seconds == 120:
                raise RuntimeError('ethanol interrupted')
            self.advance(seconds)
        self.sleep.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, 'ethanol interrupted'):
            protocol.cleanup(self.runner, PARAMS)
        self.assertTrue(any(e[0:2] == ('wait', 5.0) for e in self.events))
        self.assertFalse(self.runner._mb_ethanol_needs_settle)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        for tag, target in protocol.VALVE_ELECTROLYSIS_TAGS:
            self.assertEqual(self.runner.opc.values[tag], target)

    def test_failed_settle_wait_keeps_valve_guard_and_pumps_stopped(self):
        self.prepare_cleaning()
        def wait(seconds):
            if seconds == 5:
                raise RuntimeError('settle interrupted')
            self.advance(seconds)
        self.sleep.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, 'settle interrupted'):
            protocol.cleanup(self.runner, PARAMS)
        self.assertTrue(self.runner._mb_ethanol_needs_settle)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_WATER_TAG], 0)
        self.assertFalse(self.runner._mb_feed_shutdown_confirmed)
        for action in (lambda: protocol._set_flow_mode(self.runner, 'electrolysis'),
                       lambda: protocol._set_uvvis_route(self.runner, 'waste'),
                       lambda: protocol._select_cell(self.runner, 1)):
            with self.assertRaisesRegex(RuntimeError, 'valve movement is blocked'):
                action()

    def test_single_pump_stop_completes_a_pending_ethanol_hold(self):
        self.runner._mb_ethanol_needs_settle = True
        self.runner.opc.values[protocol.PUMP_CLEANING_TAG] = 2
        protocol._stop_cleaning_pump(self.runner)
        self.assertEqual(self.now, 5.0)
        self.assertEqual(self.runner.opc.values[protocol.PUMP_CLEANING_TAG], 0)
        self.assertFalse(self.runner._mb_ethanol_needs_settle)

    def test_ethanol_settle_override_is_validated_and_used(self):
        self.prepare_cleaning()
        self.runner.adapter_config = {'cleaning_pump_stop_settle_s': 7.5}
        protocol.cleanup(self.runner, PARAMS)
        self.assertTrue(any(e[0:2] == ('wait', 7.5) for e in self.events))
        self.runner._mb_feed_shutdown_confirmed = True
        self.runner.adapter_config = {'cleaning_pump_stop_settle_s': 0}
        self.events.clear()
        with self.assertRaisesRegex(ValueError, 'finite and positive'):
            protocol.cleanup(self.runner, PARAMS)
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
