"""Save before the first experiment, then restore in a new Streamlit session."""

import json
import shutil
import unittest
import uuid
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tests.test_campaign_setup import configured_state, VARIABLES


class CampaignSetupUITests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"setup_ui_{uuid.uuid4().hex}"
        self.root.mkdir()
        self.stack = ExitStack()
        self.stack.enter_context(patch("core.campaigns.templates.DEFAULT_TEMPLATE_DIR", str(self.root)))
        self.runner_factory = self.stack.enter_context(patch(
            "core.hardware.experimental_run.replace_experiment_runner",
            side_effect=AssertionError("Saving/loading a setup must not initialize a runner"),
        ))
        self.stack.enter_context(patch("core.hardware.experimental_run.MultiportValve", side_effect=AssertionError("Unexpected valve access")))
        self.stack.enter_context(patch("core.hardware.experimental_run.SPMPump", side_effect=AssertionError("Unexpected pump access")))
        self.stack.enter_context(patch("requests.get", side_effect=AssertionError("Unexpected network request")))

    def tearDown(self):
        self.stack.close()
        assert self.root.resolve().parent == Path(__file__).resolve().parent
        shutil.rmtree(self.root)

    def check(self, app):
        self.assertEqual([item.message for item in app.exception], [])

    def make_app(self, mode, state=None):
        filename = "Single_Objective.py" if mode == "single" else "Multi_Objective.py"
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / filename), default_timeout=30)
        app.session_state["simulation_mode"] = "full"
        for key, value in (state or {}).items():
            app.session_state[key] = value
        app.run()
        self.check(app)
        return app

    def roundtrip(self, mode, view):
        name = f"{mode} {view} draft"
        state = configured_state(mode, view)
        # A real-hardware setup must also save/load without opening devices.
        if view == "Quick":
            state["simulation_mode"] = "off"
        app = self.make_app(mode, state)
        self.assertEqual(app.button(key=f"{mode}_save_setup").label, "Save Campaign Setup")
        self.assertEqual(app.button(key=f"{mode}_load_setup").label, "Load Campaign Setup")
        app.text_input(key=f"{mode}_setup_name").set_value(name)
        app.button(key=f"{mode}_save_setup").click().run()
        self.check(app)
        saved = self.root / f"{name}.json"
        self.assertTrue(saved.exists())
        payload = json.loads(saved.read_text())
        self.assertEqual(payload["variables"][-1]["categories"], ["A", "B", "C"])
        self.assertEqual(payload["metadata"]["experiment_name"], "Draft experiment")

        # An independent session has no experiment records, optimizer, or devices.
        restored = self.make_app(mode)
        restored.selectbox(key=f"{mode}_saved_setup_select").select(name).run()
        restored.button(key=f"{mode}_load_setup").click().run()
        self.check(restored)
        self.assertEqual(restored.session_state["variables"], VARIABLES)
        self.assertEqual(restored.number_input(key=f"{mode}_initial_experiments_input").value, 7)
        self.assertEqual(restored.number_input(key=f"{mode}_total_iterations_input").value, 23)
        self.assertEqual(restored.text_input(key=f"{mode}_campaign_name").value, "Draft experiment")
        self.assertEqual(restored.date_input(key=f"{mode}_campaign_date").value, date(2026, 11, 17))
        self.assertEqual(restored.text_area(key=f"{mode}_campaign_notes").value, state["experiment_notes"])
        self.assertEqual(restored.session_state["initial_experiments"], 7)
        self.assertEqual(restored.session_state["total_iterations"], 23)
        self.assertEqual(restored.session_state["random_seed"], 81)
        self.assertEqual(restored.session_state[f"{mode}_reference_categories"], {"catalyst": "B"})
        self.assertEqual(restored.selectbox(key=f"{mode}_protocol_script_select").value, "example_protocol.py")
        self.assertEqual(restored.selectbox(key=f"{mode}_simulation_mode_select").value, state["simulation_mode"])
        self.assertEqual(restored.session_state["experiment_data"], [])
        self.assertEqual(restored.session_state["iteration"], 0)
        self.assertIsNone(restored.session_state["optimizer"])
        self.assertIsNone(restored.session_state["runner"])
        self.assertFalse(restored.session_state["optimization_running"])
        if mode == "single":
            self.assertEqual(restored.selectbox(key="single_response_select").value, "Concentration")
        else:
            self.assertEqual(restored.multiselect(key="multi_objectives_select").value, ["Yield", "Throughput"])
            self.assertEqual(restored.selectbox(key="Throughput_direction").value, "minimize")

        # Restoring also replaces values already edited in the current form.
        restored.number_input(key=f"{mode}_total_iterations_input").set_value(55).run()
        if mode == "multi":
            restored.selectbox(key="Throughput_direction").select("maximize").run()
        restored.button(key=f"{mode}_load_setup").click().run()
        self.check(restored)
        self.assertEqual(restored.number_input(key=f"{mode}_total_iterations_input").value, 23)
        if mode == "multi":
            self.assertEqual(restored.selectbox(key="Throughput_direction").value, "minimize")
        self.runner_factory.assert_not_called()

    def test_single_quick(self):
        self.roundtrip("single", "Quick")

    def test_single_advanced(self):
        self.roundtrip("single", "Advanced")

    def test_multi_quick(self):
        self.roundtrip("multi", "Quick")

    def test_multi_advanced(self):
        self.roundtrip("multi", "Advanced")


if __name__ == "__main__":
    unittest.main()
