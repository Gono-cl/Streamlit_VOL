"""Save/load setup controls, available before any experiment is executed."""

import streamlit as st

from . import templates
from .setup import build_setup_payload, setup_session_updates
from core.hardware.connections import close_runner
from core.hardware.protocol_scripts import list_protocol_scripts


def _load_setup(mode, name, base_dir):
    if st.session_state.get("optimization_running", False):
        st.warning("Stop the current campaign before loading another setup.")
        return
    if name == "None":
        st.warning("Choose a saved campaign setup first.")
        return
    try:
        payload = templates.load_campaign_template(name, base_dir=base_dir)
        if payload is None:
            raise ValueError("This campaign setup could not be found.")
        updates = setup_session_updates(payload, mode)
        script = updates.get("running_protocol_script")
        if script and script not in list_protocol_scripts():
            raise ValueError(f"Saved protocol file '{script}' is missing. Restore that file before loading this setup.")
        close_runner(st.session_state.get("runner"))
        # The callback runs before widgets are rendered, so keyed controls can
        # be restored directly without Streamlit's post-render mutation errors.
        for key in list(st.session_state):
            if key.startswith(f"{mode}_reference_") or (
                key.startswith(f"{mode}_repro_") and key not in updates
            ):
                st.session_state.pop(key, None)
        for key in (
            "reuse_candidates", "reuse_candidates_mo", "preloaded_rows_so", "preloaded_rows_mo",
            "initial_design_preview", "initial_design_preview_bounds", "stop_requested",
            f"_{mode}_deferred_session_updates", f"_{mode}_repro_from_studio",
        ):
            st.session_state.pop(key, None)
        st.session_state.update(updates)
        st.session_state[f"{mode}_protocol_script_select"] = script or "None"
        st.session_state[f"{mode}_setup_name"] = name
        st.session_state[f"{mode}_setup_notice"] = f"Loaded campaign setup: {name}. Ready to edit or start."
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        st.error(f"Could not load campaign setup: {exc}")


def render_campaign_setup(mode, experiment_name, experiment_date, notes):
    st.markdown("### Save / Load Campaign Setup")
    st.caption("Save your variables and settings before running any experiments. Saved setups remain available after restarting VOL. Loading a setup does not start a run.")
    notice = st.session_state.pop(f"{mode}_setup_notice", None)
    if notice:
        st.success(notice)
    base_dir = templates.DEFAULT_TEMPLATE_DIR
    try:
        saved = templates.list_campaign_templates(mode=mode, base_dir=base_dir)
    except OSError as exc:
        st.error(f"Could not read saved campaign setups: {exc}")
        return
    selected_key = f"{mode}_saved_setup_select"
    pending = st.session_state.pop(f"{mode}_setup_pending_select", None)
    if pending in saved:
        st.session_state[selected_key] = pending
    if st.session_state.get(selected_key, "None") not in ["None"] + saved:
        st.session_state[selected_key] = "None"
    running = bool(st.session_state.get("optimization_running", False))
    load_column, save_column = st.columns(2)
    with load_column:
        selected = st.selectbox("Saved Campaign Setup", ["None"] + saved, key=selected_key, disabled=running)
        st.button("Load Campaign Setup", key=f"{mode}_load_setup", disabled=running,
                  on_click=_load_setup, args=(mode, selected, base_dir))
    with save_column:
        name = st.text_input("Campaign setup name", value=experiment_name,
                             key=f"{mode}_setup_name", disabled=running)
        if st.button("Save Campaign Setup", key=f"{mode}_save_setup", disabled=running):
            if not name.strip():
                st.warning("Enter a name for the campaign setup.")
            else:
                try:
                    payload = build_setup_payload(mode, name, st.session_state,
                                                  experiment_name, experiment_date, notes)
                    templates.save_campaign_template(name, payload, base_dir=base_dir)
                except (OSError, ValueError, TypeError) as exc:
                    st.error(f"Could not save campaign setup: {exc}")
                else:
                    st.session_state[f"{mode}_setup_notice"] = f"Saved campaign setup: {name}."
                    st.session_state[f"{mode}_setup_pending_select"] = templates.sanitize_template_name(name)
                    st.rerun()
