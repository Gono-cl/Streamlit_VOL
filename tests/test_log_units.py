"""CSV and download unit labels preserve values, legacy rows and model keys."""
import csv
from io import BytesIO, StringIO
import math
import os
import unittest
from unittest.mock import patch

import pandas as pd

from core.utils.log_units import unit_labeled_row, unit_labeled_dataframe, variable_units
from core.utils.export_tools import export_to_csv, export_to_excel
from tests import test_uvvis_integration as helpers


class UnitLabelTests(unittest.TestCase):
    def test_mb_variables_use_protocol_units_and_categories_stay_labels(self):
        variables = [('temperature', 10, 50, 'degC'),
                     ('cell', ['EC1', 'EC2'], None, 'unwanted', 'categorical'),
                     ('substrate_concentration', 100, 500, 'wrong', 'continuous')]
        units = variable_units(variables, 'running_protocols/MB_6_variables.py')
        row = {'current_density': 5, 'applied_charge': 3, 'substrate_concentration': 100,
               'electrolyte_concentration': 0.1, 'cell': 'EC1', 'temperature': 30}
        labelled = unit_labeled_row(row, units=units)
        self.assertEqual(labelled['current_density (mA/cm2)'], 5)
        self.assertEqual(labelled['applied_charge (F/mol)'], 3)
        self.assertEqual(labelled['substrate_concentration (mg/L)'], 100)
        self.assertEqual(labelled['electrolyte_concentration (mol/L)'], 0.1)
        self.assertEqual(labelled['temperature (degC)'], 30)
        self.assertEqual(labelled['cell'], 'EC1')
        self.assertIn('current_density', row)

    def test_concentration_units_come_from_each_record(self):
        for unit in ('mg/L', 'mM', 'mol/L'):
            row = {'Concentration Unit': unit, 'Value': 4, 'Measured Concentration': 4,
                   'Corrected Concentration': 8, 'Inlet Concentration': 20, 'Outlet Concentration': 8,
                   'UV/VIS Concentration': 8, 'Concentration': 8}
            result = unit_labeled_row(row)
            for key, value in row.items():
                self.assertEqual(result[key if key == 'Concentration Unit' else f'{key} ({unit})'], value)

    def test_absorbance_and_existing_electrical_units_remain_consistent(self):
        row = {'Absorbance': 0.5, 'UV/VIS Absorbance': 0.5, 'UV/VIS Absorbance SD': 0.001,
               'BKK Read Current (A)': 0.02, 'BKK Read Voltage (V)': 7, 'Fitted Drift (AU)': 0.002,
               'Timestamp': '2026-11-23T11:11:18+00:00'}
        result = unit_labeled_row(row)
        self.assertEqual(result['Absorbance (AU)'], 0.5)
        self.assertEqual(result['UV/VIS Absorbance SD (AU)'], 0.001)
        self.assertEqual(result['BKK Read Current (A)'], 0.02)
        self.assertIn('Timestamp (UTC)', result)
        self.assertEqual(unit_labeled_row(result), result)

    def test_mixed_cv_sd_and_concentration_units_get_separate_columns(self):
        frame = pd.DataFrame([
            {'Value': 4, 'Concentration Unit': 'mg/L', 'Stability Limit': 5, 'Stability Units': '%'},
            {'Value': 0.2, 'Concentration Unit': 'mM', 'Stability Limit': 0.002, 'Stability Units': 'AU'}])
        result = unit_labeled_dataframe(frame)
        self.assertEqual(result.loc[0, 'Value (mg/L)'], 4)
        self.assertTrue(pd.isna(result.loc[1, 'Value (mg/L)']))
        self.assertEqual(result.loc[1, 'Value (mM)'], 0.2)
        self.assertEqual(result.loc[0, 'Stability Limit (%)'], 5)
        self.assertEqual(result.loc[1, 'Stability Limit (AU)'], 0.002)

    def test_unknown_units_and_naive_timestamps_are_not_guessed(self):
        result = unit_labeled_row({'Value': 12.5, 'Concentration Unit': '', 'Timestamp': '2026-11-23 12:00:00'})
        self.assertIn('Value', result)
        self.assertIn('Timestamp', result)
        result = unit_labeled_row({'Value': 0.2, 'Concentration Unit': math.nan, 'Stability Limit': 5, 'Stability Units': math.nan})
        self.assertIn('Value', result)
        self.assertIn('Stability Limit', result)
        self.assertFalse(any('(nan)' in key for key in result))

    def test_objective_and_measurement_units_and_dataframe_are_preserved(self):
        frame = pd.DataFrame([{'cell': '01', 'electrolyte': 'NA', 'current_density': 5,
                               'Measurement': 90, 'Degradation': 90, 'Concentration': 2,
                               'Concentration Unit': 'mg/L'}])
        original = frame.copy(deep=True)
        result = unit_labeled_dataframe(frame, protocol_script='MB_6_variables.py', objective='Degradation')
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual(result.loc[0, 'Measurement (%)'], 90)
        self.assertEqual(result.loc[0, 'Degradation (%)'], 90)
        self.assertEqual(result.loc[0, 'Concentration (mg/L)'], 2)
        self.assertEqual(result.loc[0, 'cell'], '01')
        self.assertEqual(result.loc[0, 'electrolyte'], 'NA')

    def test_duplicate_unit_titles_fail_instead_of_losing_values(self):
        with self.assertRaisesRegex(ValueError, 'duplicate column'):
            unit_labeled_row({'Absorbance': 0.2, 'Absorbance (AU)': 0.3})
        result = unit_labeled_row({'Absorbance': 0.2, 'Absorbance (AU)': ''})
        self.assertEqual(result['Absorbance (AU)'], 0.2)

    def test_empty_export_has_parameter_and_objective_units(self):
        result = unit_labeled_dataframe(pd.DataFrame(columns=['current_density', 'cell', 'Degradation']),
                                        protocol_script='MB_6_variables.py')
        self.assertEqual(list(result), ['current_density (mA/cm2)', 'cell', 'Degradation (%)'])

    def test_csv_and_excel_downloads_use_the_same_unit_labels(self):
        frame = pd.DataFrame([{'current_density': 5, 'Absorbance': 0.2, 'Concentration Unit': 'mg/L', 'Value': 10}])
        options = {'protocol_script': 'MB_6_variables.py'}
        with patch('core.utils.export_tools.st.download_button') as download:
            export_to_csv(frame, 'test.csv', **options)
            rows = list(csv.DictReader(StringIO(download.call_args.kwargs['data'].decode('utf-8-sig'))))
            self.assertEqual(rows[0]['Value (mg/L)'], '10')
            self.assertEqual(rows[0]['Absorbance (AU)'], '0.2')
            export_to_excel(frame, 'test.xlsx', **options)
            # Inspect the actual workbook without requiring an Excel process.
            from zipfile import ZipFile
            with ZipFile(BytesIO(download.call_args.kwargs['data'])) as archive:
                strings = archive.read('xl/sharedStrings.xml').decode('utf-8')
                self.assertIn('current_density (mA/cm2)', strings)
                self.assertIn('Value (mg/L)', strings)


class RawLogUnitTests(unittest.TestCase):
    setUp = helpers.UVVisIntegrationTests.setUp
    tearDown = helpers.UVVisIntegrationTests.tearDown
    runner = helpers.UVVisIntegrationTests.runner
    write_model = helpers.UVVisIntegrationTests.write_model

    def buffer(self):
        runner = self.runner()
        runner.running_protocol_script = 'MB_6_variables.py'
        runner.logging_variable_units = variable_units(protocol_script=runner.running_protocol_script)
        runner.full_measurement_log = [
            {'Iteration': 2, 'current_density': 5, 'substrate_concentration': 100,
             'Value': 2.5, 'Absorbance': 0.05, 'Concentration Unit': 'mg/L',
             'Stability Limit': 0.002, 'Stability Units': 'AU', 'cell': 'EC1'}]
        return runner

    def save(self, runner, legacy=None):
        folder = self.root / 'raw_measurements'
        folder.mkdir(exist_ok=True)
        path = folder / 'units_uvvis_measurements.csv'
        if legacy is not None:
            path.write_text(legacy, encoding='utf-8')
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            runner.save_full_measurements_to_csv('units')
        finally:
            os.chdir(previous)
        return path

    def test_new_log_has_units_and_does_not_change_buffered_row_keys(self):
        runner = self.buffer()
        original_row = runner.full_measurement_log[0]
        path = self.save(runner)
        rows = list(csv.DictReader(path.open(newline='', encoding='utf-8-sig')))
        self.assertEqual(rows[0]['Value (mg/L)'], '2.5')
        self.assertEqual(rows[0]['substrate_concentration (mg/L)'], '100')
        self.assertEqual(rows[0]['Absorbance (AU)'], '0.05')
        self.assertIn('Value', original_row)
        self.assertEqual(runner.full_measurement_log, [])

    def test_known_legacy_units_upgrade_atomically_and_repeated_append_works(self):
        path = self.save(self.buffer(), 'Iteration,Value,Concentration Unit,Absorbance,Stability Limit,Stability Units\n1,3.5,mg/L,0.07,5,%\n')
        self.save(self.buffer())
        rows = list(csv.DictReader(path.open(newline='', encoding='utf-8-sig')))
        self.assertEqual(len(rows), 3)
        self.assertNotIn('Value', rows[0])
        self.assertEqual(rows[0]['Value (mg/L)'], '3.5')
        self.assertEqual(rows[0]['Stability Limit (%)'], '5')
        self.assertEqual(rows[1]['Stability Limit (AU)'], '0.002')
        self.assertTrue(all(None not in row and None not in row.values() for row in rows))

    def test_unknown_legacy_unit_is_preserved_on_multiple_appends(self):
        path = self.save(self.buffer(), 'Iteration,Value\n1,12.5\n')
        self.save(self.buffer())
        rows = list(csv.DictReader(path.open(newline='', encoding='utf-8-sig')))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['Value'], '12.5')
        self.assertEqual(rows[0]['Value (mg/L)'], '')
        self.assertEqual(rows[2]['Value'], '')
        self.assertEqual(rows[2]['Value (mg/L)'], '2.5')

    def test_failed_replace_preserves_file_and_buffer(self):
        runner = self.buffer()
        original = 'Iteration,Value,Concentration Unit\n1,3.5,mg/L\n'
        folder = self.root / 'raw_measurements'
        folder.mkdir()
        path = folder / 'units_uvvis_measurements.csv'
        path.write_text(original, encoding='utf-8')
        buffered = list(runner.full_measurement_log)
        with patch('core.hardware.experimental_run.os.replace', side_effect=OSError('locked')):
            with self.assertRaisesRegex(OSError, 'locked'):
                self.save(runner)
        self.assertEqual(path.read_text(encoding='utf-8'), original)
        self.assertEqual(runner.full_measurement_log, buffered)
        self.assertEqual(list(folder.glob('*.tmp')), [])

    def test_short_legacy_row_is_rejected_without_overwrite(self):
        runner = self.buffer()
        original = 'Iteration,Value,Concentration Unit\n1,12.5\n'
        with self.assertRaisesRegex(ValueError, 'inconsistent columns'):
            self.save(runner, original)
        path = self.root / 'raw_measurements/units_uvvis_measurements.csv'
        self.assertEqual(path.read_text(encoding='utf-8'), original)
        self.assertTrue(runner.full_measurement_log)


if __name__ == '__main__':
    unittest.main()
