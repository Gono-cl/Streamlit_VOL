"""Persist and restore configuration without an optimizer or experiment data."""

import copy
import json
import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path

from core.campaigns.setup import build_setup_payload, setup_session_updates
from core.campaigns.templates import save_campaign_template, load_campaign_template, list_campaign_templates


VARIABLES = [
    ("temperature", 20.0, 80.0, "C", "continuous"),
    ("catalyst", ["A", "B", "C"], None, "", "categorical"),
]


def configured_state(mode, view="Quick"):
    return {
        "variables": VARIABLES, "initial_experiments": 7, "total_iterations": 23,
        "acq_func": "LCB", "init_strategy": "LHS", "random_seed": 81,
        "response_to_optimize": "Concentration", "objectives": ["Yield", "Throughput"],
        "Yield_direction": "maximize", "Throughput_direction": "minimize",
        "simulation_mode": "full", "opc_url": "http://example.invalid:1234",
        "use_autosampler": True, "volume_to_collect": 2.5,
        "running_protocol_script": "example_protocol.py", "process_adapter": "echem_4pump",
        "process_adapter_config": {"spm_waste_port": 5},
        "measurement_source_prefix": "test-prefix", "measurement_source_signal": "test-signal",
        "run_name": "Draft experiment", "experiment_date": date(2026, 11, 17),
        "experiment_notes": "A setup saved before any experiments.",
        f"{mode}_ui_mode": view, f"{mode}_reference_categories": {"catalyst": "B"},
        f"{mode}_bo_qc_enabled": True, f"{mode}_bo_qc_every_n_runs": 4,
        f"{mode}_repro_n_points": 6,
    }


class CampaignSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"setup_store_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        assert self.root.resolve().parent == Path(__file__).resolve().parent
        shutil.rmtree(self.root)

    def payload(self, mode="single"):
        state = configured_state(mode)
        return build_setup_payload(mode, "Saved setup", state, state["run_name"],
                                   state["experiment_date"], state["experiment_notes"])

    def test_saved_setup_roundtrip_needs_no_experiment_files(self):
        for mode in ("single", "multi"):
            with self.subTest(mode=mode):
                name = f"{mode} setup"
                payload = self.payload(mode)
                path = save_campaign_template(name, payload, base_dir=self.root)
                loaded = load_campaign_template(name, base_dir=self.root)
                updates = setup_session_updates(loaded, mode)
                self.assertTrue(Path(path).is_file())
                self.assertEqual(updates["variables"], VARIABLES)
                self.assertEqual(updates["initial_experiments"], 7)
                self.assertEqual(updates["total_iterations"], 23)
                self.assertEqual(updates["experiment_date"], date(2026, 11, 17))
                self.assertEqual(updates["experiment_notes"], configured_state(mode)["experiment_notes"])
                self.assertFalse(updates["optimization_running"])
                self.assertEqual(updates["experiment_data"], [])
                self.assertEqual(updates["iteration"], 0)
                self.assertIsNone(updates["optimizer"])
                self.assertIsNone(updates["runner"])
                self.assertIn(name, list_campaign_templates(mode=mode, base_dir=self.root))
        self.assertFalse(list(self.root.rglob("*.pkl")))
        self.assertFalse(list(self.root.rglob("*.csv")))

    def test_multiobjective_directions_and_widget_keys_are_restored(self):
        updates = setup_session_updates(self.payload("multi"), "multi")
        self.assertEqual(updates["objectives"], ["Yield", "Throughput"])
        self.assertEqual(updates["multi_objectives_select"], ["Yield", "Throughput"])
        self.assertEqual(updates["Yield_direction"], "maximize")
        self.assertEqual(updates["Throughput_direction"], "minimize")
        self.assertEqual(updates["multi_campaign_name"], "Draft experiment")
        self.assertEqual(updates["multi_reference_catalyst"], "B")
        self.assertEqual(updates["multi_repro_n_points"], 6)

    def test_legacy_template_without_type_or_metadata_remains_loadable(self):
        legacy = {
            "schema_version": 1, "mode": "single", "name": "Older saved setup",
            "variables": [{"name": "temperature", "lower": 10, "upper": 30, "unit": "C"}],
            "optimization": {"response": "Yield"}, "hardware": {},
        }
        updates = setup_session_updates(legacy, "single")
        self.assertEqual(updates["variables"], [("temperature", 10.0, 30.0, "C", "continuous")])
        self.assertEqual(updates["run_name"], "Older saved setup")
        self.assertEqual(updates["single_response_select"], "Yield")
        self.assertFalse(updates["optimization_running"])

    def test_setup_mode_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            setup_session_updates(self.payload("single"), "multi")

    def test_invalid_settings_are_rejected_without_mutating_the_payload(self):
        invalid = self.payload()
        invalid["optimization"]["total_iterations"] = 0
        before = copy.deepcopy(invalid)
        with self.assertRaisesRegex(ValueError, "total_iterations"):
            setup_session_updates(invalid, "single")
        self.assertEqual(invalid, before)

    def test_a_failed_save_preserves_the_previous_setup(self):
        path = Path(save_campaign_template("Stable setup", self.payload(), base_dir=self.root))
        before = path.read_bytes()
        invalid = self.payload()
        invalid["not_serializable"] = object()
        with self.assertRaises(TypeError):
            save_campaign_template("Stable setup", invalid, base_dir=self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_single_and_multi_setups_cannot_overwrite_each_other(self):
        path = Path(save_campaign_template("Shared name", self.payload("single"), base_dir=self.root))
        with self.assertRaisesRegex(ValueError, "another campaign type"):
            save_campaign_template("Shared name", self.payload("multi"), base_dir=self.root)
        self.assertEqual(json.loads(path.read_text())["mode"], "single")

    def test_same_mode_setup_can_be_updated(self):
        save_campaign_template("Updated setup", self.payload(), base_dir=self.root)
        updated = self.payload()
        updated["optimization"]["total_iterations"] = 40
        save_campaign_template("Updated setup", updated, base_dir=self.root)
        self.assertEqual(load_campaign_template("Updated setup", base_dir=self.root)["optimization"]["total_iterations"], 40)


if __name__ == "__main__":
    unittest.main()
