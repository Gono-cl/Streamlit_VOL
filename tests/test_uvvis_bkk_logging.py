"""Associate individual fresh UV/Vis spectra with read-only BKK OPC snapshots."""

import contextlib
import csv
import io
import json
import os
import unittest
from datetime import datetime
from unittest.mock import Mock, call, patch

from core.hardware.experimental_run import ExperimentRunner
from core.hardware.uvvis_measurement import UVVisMeasurement
from running_protocols import MB_6_variables as protocol
from tests import test_uvvis_integration as integration_helpers
from tests import test_uvvis_dilution as dilution_helpers


class BKKSnapshotTests(unittest.TestCase):
    setUp = integration_helpers.UVVisIntegrationTests.setUp
    tearDown = integration_helpers.UVVisIntegrationTests.tearDown
    write_model = integration_helpers.UVVisIntegrationTests.write_model
    runner = integration_helpers.UVVisIntegrationTests.runner

    def configured_runner(self):
        runner = self.runner()
        runner.protocol_uvvis_acquisition_metadata_fn = protocol.uvvis_acquisition_metadata
        return runner

    def test_each_spectrum_receives_its_own_values_using_all_full_tag_ids(self):
        runner = self.configured_runner()
        counter = 0
        tags = [tag for _, tag in protocol.BKK_TELEMETRY_TAGS]

        def read(tag, **kwargs):
            nonlocal counter
            group, field = divmod(counter, 4)
            self.assertEqual(tag, tags[field])
            self.assertEqual(kwargs, {"timeout": 1.0})
            self.assertEqual(len(runner._uvvis_measurement.reader.acquisitions), group + 1)
            counter += 1
            return [30.0, 5.0 + group, 0.02, 0.018 + group * 0.001][field]

        runner.opc.read_value.side_effect = read
        with integration_helpers.ExportClock(self.root, [1.4, 1.401, 1.399]).running():
            runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        rows = runner.full_measurement_log
        self.assertEqual(counter, 12)
        self.assertEqual([r["BKK Read Voltage (V)"] for r in rows], [5.0, 6.0, 7.0])
        for i, row in enumerate(rows):
            self.assertAlmostEqual(row["BKK Read Current (A)"], 0.018 + i * 0.001)
            self.assertEqual(row["BKK Set Voltage (V)"], 30)
            self.assertEqual(row["BKK Set Current (A)"], 0.02)
            self.assertEqual(row["BKK Telemetry Status"], "ok")
            self.assertEqual(row["BKK Telemetry Errors"], "")
            optical = datetime.fromisoformat(row["Spectrum Read Timestamp (UTC)"])
            start = datetime.fromisoformat(row["BKK Telemetry Start (UTC)"])
            end = datetime.fromisoformat(row["BKK Telemetry End (UTC)"])
            self.assertIsNotNone(optical.tzinfo)
            self.assertLessEqual(optical, start)
            self.assertLessEqual(start, end)
        self.assertEqual(tags, ["Hitec_OPC_DA20_Server->E_CHEM:BKK_P.SETVOLT",
                                "Hitec_OPC_DA20_Server->E_CHEM:BKK_P.VOLT",
                                "Hitec_OPC_DA20_Server->E_CHEM:BKK_P.SETCURR",
                                "Hitec_OPC_DA20_Server->E_CHEM:BKK_P.CURR"])
        runner.opc.write_value.assert_not_called()

    def test_missing_or_invalid_values_are_blank_and_do_not_reject_optical_result(self):
        runner = self.configured_runner()

        def read(tag, **kwargs):
            if tag.endswith(".SETVOLT"):
                return "30,000"
            if tag.endswith(".VOLT"):
                raise OSError("Read failed")
            if tag.endswith(".SETCURR"):
                return "0,020"
            return float("nan")

        runner.opc.read_value.side_effect = read
        with integration_helpers.ExportClock(self.root, [1.4] * 3).running():
            result = runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        self.assertAlmostEqual(result["Concentration"], 0.2)
        for row in runner.full_measurement_log:
            self.assertTrue(row["Accepted"])
            self.assertIsNone(row["BKK Read Voltage (V)"])
            self.assertIsNone(row["BKK Read Current (A)"])
            self.assertEqual(row["BKK Set Voltage (V)"], 30.0)
            self.assertEqual(row["BKK Set Current (A)"], 0.02)
            self.assertEqual(row["BKK Telemetry Status"], "partial")
            self.assertEqual(len(json.loads(row["BKK Telemetry Errors"])), 2)
        runner.opc.write_value.assert_not_called()

    def test_unavailable_and_real_zero_values_remain_distinct(self):
        runner = self.configured_runner()
        runner.opc.read_value.side_effect = None
        for raw, status in [(None, "unavailable"), (False, "unavailable"), (0, "ok")]:
            with self.subTest(raw=raw), contextlib.redirect_stdout(io.StringIO()):
                runner.opc.read_value.return_value = raw
                result = protocol.uvvis_acquisition_metadata(runner, {})
                self.assertEqual(result["BKK Telemetry Status"], status)
                for field, _ in protocol.BKK_TELEMETRY_TAGS:
                    self.assertEqual(result[field], 0.0 if raw is not None and raw is not False else None)

    def test_extra_optical_readings_also_capture_new_electrical_snapshots(self):
        runner = self.configured_runner()
        runner.opc.read_value.side_effect = None
        runner.opc.read_value.return_value = 1.0
        with integration_helpers.ExportClock(self.root, [2.9, 1.4, 1.4, 1.4]).running():
            runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        self.assertEqual(len(runner.full_measurement_log), 4)
        self.assertEqual(runner.opc.read_value.call_count, 16)
        self.assertTrue(all(r["BKK Telemetry Status"] == "ok" for r in runner.full_measurement_log))

    def test_partial_optical_timeout_preserves_the_captured_snapshot_and_restores_callback(self):
        runner = self.configured_runner()
        runner.opc.read_value.side_effect = None
        runner.opc.read_value.return_value = 0.02
        with integration_helpers.ExportClock(self.root, [1.4]).running(), self.assertRaises(TimeoutError):
            runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        self.assertEqual(len(runner.full_measurement_log), 1)
        self.assertEqual(runner.opc.read_value.call_count, 4)
        row = runner.full_measurement_log[0]
        self.assertFalse(row["Accepted"])
        self.assertEqual(row["Range Status"], "acquisition_failed")
        self.assertEqual(row["BKK Read Current (A)"], 0.02)
        self.assertIsNone(runner._uvvis_measurement.reader.acquisition_metadata_callback)

    def test_callback_failure_does_not_discard_a_fresh_spectrum(self):
        runner = self.configured_runner()
        runner.protocol_uvvis_acquisition_metadata_fn = Mock(side_effect=OSError("Telemetry unavailable"))
        with integration_helpers.ExportClock(self.root, [1.4] * 3).running():
            result = runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        self.assertAlmostEqual(result["Concentration"], 0.2)
        self.assertTrue(all("Telemetry unavailable" in r["Acquisition Telemetry Error"]
                            for r in runner.full_measurement_log))
        self.assertIsNone(runner._uvvis_measurement.reader.acquisition_metadata_callback)

    def test_standalone_measurement_after_runner_does_not_reuse_telemetry_callback(self):
        runner = self.configured_runner()
        runner.opc.read_value.side_effect = None
        runner.opc.read_value.return_value = 1
        with integration_helpers.ExportClock(self.root, [1.4] * 6).running():
            runner.collect_measurements(parameters={})
            runner.opc.read_value.reset_mock()
            result = runner._uvvis_measurement.measure()
        self.assertTrue(result["accepted"])
        runner.opc.read_value.assert_not_called()

    def test_existing_raw_csv_preserves_old_rows_and_adds_electrical_columns(self):
        runner = self.configured_runner()
        runner.opc.read_value.side_effect = None
        runner.opc.read_value.return_value = 0.02
        with integration_helpers.ExportClock(self.root, [1.4] * 3).running():
            runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            folder = self.root / "raw_measurements"
            folder.mkdir()
            path = folder / "saved_uvvis_measurements.csv"
            path.write_text("Iteration,Value\n1,12.5\n", encoding="utf-8")
            runner.save_full_measurements_to_csv("saved")
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["Value"], "12.5")
            self.assertEqual(rows[0]["BKK Read Current (A)"], "")
            self.assertEqual(rows[1]["BKK Read Current (A)"], "0.02")
            self.assertTrue(all(None not in row and None not in row.values() for row in rows))
        finally:
            os.chdir(previous)

    def test_simulation_modes_do_not_read_electrical_tags(self):
        runner = self.configured_runner()
        for mode in ("full", "hybrid"):
            with self.subTest(mode=mode):
                runner.simulation_mode = mode
                self.assertEqual(protocol.uvvis_acquisition_metadata(runner, {}), {})
        runner.opc.read_value.assert_not_called()

    def test_runner_loads_the_optional_mb_metadata_hook(self):
        with patch("core.hardware.experimental_run.st.empty"), patch("core.hardware.experimental_run.st.sidebar.empty"):
            runner = ExperimentRunner(Mock(), "", simulation_mode="full", running_protocol_script="MB_6_variables.py")
        self.assertTrue(callable(runner.protocol_uvvis_acquisition_metadata_fn))
        self.assertEqual(runner.protocol_uvvis_acquisition_metadata_fn.__name__, "uvvis_acquisition_metadata")

    def test_optional_metadata_cannot_overwrite_optical_values(self):
        runner = self.configured_runner()
        runner.protocol_uvvis_acquisition_metadata_fn = lambda *args: {"Value": 999, "Accepted": False}
        with integration_helpers.ExportClock(self.root, [1.4] * 3).running():
            runner.run_experiment({"substrate_concentration": 0.5}, objectives=["Concentration"])
        for row in runner.full_measurement_log:
            self.assertAlmostEqual(row["Value"], 0.2)
            self.assertTrue(row["Accepted"])


class BKKDilutionLoggingTests(unittest.TestCase):
    setUp = dilution_helpers.AutoDilutionTests.setUp
    tearDown = dilution_helpers.AutoDilutionTests.tearDown
    write_model = dilution_helpers.AutoDilutionTests.write_model
    run_measurement = dilution_helpers.AutoDilutionTests.run_measurement

    def test_each_dilution_attempt_captures_electrical_values_before_water_stops(self):
        original = self.runner.opc.read_value.side_effect

        def read(tag, **kwargs):
            if tag.endswith("BKK_P.VOLT"):
                return 5.0 + self.env.water_flow
            if tag.endswith("BKK_P.CURR") or tag.endswith("BKK_P.SETCURR"):
                return 0.02
            if tag.endswith("BKK_P.SETVOLT"):
                return 30.0
            return original(tag, **kwargs)

        self.runner.opc.read_value.side_effect = read
        result = self.run_measurement()
        self.assertAlmostEqual(result["Concentration"], 200, places=6)
        rows = self.runner.full_measurement_log
        direct = [r for r in rows if r["Dilution Factor"] == 1]
        diluted = [r for r in rows if r["Dilution Factor"] > 1]
        self.assertTrue(direct and diluted)
        self.assertTrue(all(r["BKK Read Voltage (V)"] == 5 for r in direct))
        self.assertTrue(all(r["BKK Read Voltage (V)"] > 5 for r in diluted))
        self.assertTrue(all(r["BKK Telemetry Status"] == "ok" for r in rows))
        self.assertEqual(self.env.water_flow, 0)


if __name__ == "__main__":
    unittest.main()
