import streamlit as st
from core.campaigns.variables import (
    clip_point, expanded_domains, optimizer_dimensions,
    parameters_from_row, point_from_row, read_campaign_data, reference_point, variable_axis, variable_domains,
)
from core.campaigns.variable_ui import render_variable_form, render_reference_categories, restore_reference_categories
from core.campaigns.setup_ui import render_campaign_setup
from core.campaigns.setup import widget_state_updates
from datetime import datetime
import numpy as np
import pandas as pd
import altair as alt
import time
from core.optimization.multiobjective import build_multiobjective_optimizer
from core.optimization.initial_design import (
    augmented_lhs_with_reuse,
    gap_aware_initial_points,
)
from core.campaigns import (
    INIT_STRATEGY_OPTIONS,
    MULTI_OBJECTIVE_OPTIONS,
    OPTIMIZER_ACQ_OPTIONS,
    VARIABLE_OPTIONS,
    variables_as_tuples,
)
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import replace_experiment_runner
from core.hardware.connections import campaign_connections, close_runner
from core.hardware.measurement_ui import render_measurement_settings
from core.hardware.uvvis_measurement import measurement_settings, measurement_session_updates
from core.hardware.process_adapters import DEFAULT_PROCESS_ADAPTER
from core.hardware.protocol_scripts import (
    list_protocol_scripts,
    load_protocol_module,
)
from core.utils.logger import StreamlitLogger
import sys

# Prevent stale StreamlitLogger stdout from previous reruns.
sys.stdout = sys.__stdout__
import os
import json
import dill as pickle
from src.repro.planner import build_initialization_points
from src.repro.repro_engine import DecisionLabel, ReplicatePattern, ReproducibilityEngine

# --- Save/Resume Section ---
SAVE_DIR = "resumable_multiobjective_runs"
os.makedirs(SAVE_DIR, exist_ok=True)
_MULTI_DEFERRED_SESSION_UPDATES_KEY = "_multi_deferred_session_updates"
REPRO_CYCLIC_LABEL = "Cyclic (1->...->N) x repeats"
REPRO_CYCLIC_LABEL_LEGACY = "Cyclic (1->...->N)x3"
REPRO_PATTERN_LABELS = [
    "Immediate (A->A)",
    "Bracketed (A->B->A)",
    REPRO_CYCLIC_LABEL,
]


def _json_compatible(value):
    if isinstance(value, dict):
        return {str(k): _json_compatible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_compatible(v) for v in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _queue_multi_deferred_updates(**updates) -> None:
    pending = st.session_state.get(_MULTI_DEFERRED_SESSION_UPDATES_KEY, {})
    if not isinstance(pending, dict):
        pending = {}
    for key, value in updates.items():
        pending[str(key)] = value
    st.session_state[_MULTI_DEFERRED_SESSION_UPDATES_KEY] = pending


def _apply_multi_deferred_updates() -> None:
    pending = st.session_state.pop(_MULTI_DEFERRED_SESSION_UPDATES_KEY, None)
    if not isinstance(pending, dict):
        return
    for key, value in pending.items():
        st.session_state[key] = value


def _multi_repro_seed_rows(repro_engine, param_names, objectives):
    """Aggregate reproducibility runs into unique multi-objective seed points."""
    aggregated = {}
    for record in repro_engine.effective_records():
        if bool(getattr(record.context, "is_sentinel", False)):
            continue
        if not bool(getattr(record.result, "success", False)):
            continue

        try:
            params = {name: record.point.values[name] for name in param_names}
        except Exception:
            continue

        output = dict(getattr(record.result, "output", {}) or {})
        obj_values = {}
        valid = True
        for obj in objectives:
            raw = output.get(obj, None)
            if raw is None:
                valid = False
                break
            try:
                val = float(raw)
            except Exception:
                valid = False
                break
            if not np.isfinite(val):
                valid = False
                break
            obj_values[obj] = val
        if not valid:
            continue

        key = tuple(params[name] for name in param_names)
        bucket = aggregated.setdefault(
            key,
            {
                "params": params,
                "objective_values": {obj: [] for obj in objectives},
            },
        )
        for obj, val in obj_values.items():
            bucket["objective_values"][obj].append(val)

    rows = []
    for bucket in aggregated.values():
        out_objectives = {}
        for obj in objectives:
            values = [v for v in bucket["objective_values"].get(obj, []) if np.isfinite(v)]
            if not values:
                out_objectives = {}
                break
            out_objectives[obj] = float(np.mean(values))
        if not out_objectives:
            continue
        rows.append(
            {
                "params": bucket["params"],
                "objectives": out_objectives,
                "source": "Reproducibility",
            }
        )
    return rows


def _estimate_repro_schedule_runs(point_count, patterns, sentinel_every_n_runs=None, cyclic_repeats=3):
    """Estimate number of executed runs for one reproducibility schedule call."""
    n_points = max(0, int(point_count))
    if n_points == 0:
        return 0

    base_runs = 0
    for pattern in (patterns or [ReplicatePattern.IMMEDIATE]):
        pattern_key = pattern.value if hasattr(pattern, "value") else str(pattern)
        if pattern_key == ReplicatePattern.IMMEDIATE.value:
            base_runs += 2 * n_points
        elif pattern_key == ReplicatePattern.BRACKETED.value:
            base_runs += 3 * n_points
        elif pattern_key == ReplicatePattern.CYCLIC.value:
            base_runs += max(1, int(cyclic_repeats)) * n_points

    if base_runs <= 0:
        return 0

    if sentinel_every_n_runs is None:
        return base_runs

    interval = int(sentinel_every_n_runs)
    if interval <= 0:
        return base_runs

    run_index = 0
    sentinel_runs = 0
    for _ in range(base_runs):
        if run_index > 0 and run_index % interval == 0:
            sentinel_runs += 1
            run_index += 1
        run_index += 1
    return base_runs + sentinel_runs


def _fmt_repro_metric(value) -> str:
    try:
        numeric = float(value)
    except Exception:
        return "-"
    if not np.isfinite(numeric):
        return "-"
    return f"{numeric:.4g}"


def _render_repro_report_panel(report: dict | None) -> None:
    if not isinstance(report, dict):
        return

    counts = dict(report.get("counts", {}) or {})
    metrics = dict(report.get("metrics", {}) or {})
    adjudication = dict(report.get("adjudication", {}) or {})
    events = [dict(event) for event in adjudication.get("events", []) if isinstance(event, dict)]
    reasons = [str(reason) for reason in (report.get("reasons", []) or []) if str(reason).strip()]

    st.markdown("#### Reproducibility Report")
    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
    col_r1.metric("Decision", str(report.get("decision", "UNKNOWN")))
    col_r2.metric("Total runs", int(counts.get("total_runs", 0)))
    col_r3.metric("Effective runs", int(counts.get("effective_runs", counts.get("successful_runs", 0))))
    col_r4.metric(
        "Flagged points",
        int(adjudication.get("confirmed_outliers", 0)) + int(adjudication.get("unresolved_events", 0)),
    )

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Replicate RSD (%)", _fmt_repro_metric(metrics.get("immediate_rsd_pct")))
    col_m2.metric("Memory shift", _fmt_repro_metric(metrics.get("memory_abs_shift_median")))
    col_m3.metric("Sentinel drift", _fmt_repro_metric(metrics.get("sentinel_drift_slope_abs")))

    if bool(adjudication.get("pass_downgraded_to_conditional", False)):
        st.warning("Result downgraded to CONDITIONAL because an extra cyclic replicate confirmed a suspicious run.")
    elif int(adjudication.get("confirmed_outliers", 0)) > 0:
        st.info("Suspicious cyclic runs were rechecked with an extra replicate.")
    elif int(adjudication.get("unresolved_events", 0)) > 0:
        st.info("Suspicious cyclic runs were rechecked but remained unresolved.")

    if reasons:
        st.caption("Reasons: " + " | ".join(reasons))

    if events:
        rows = []
        downgraded = bool(adjudication.get("pass_downgraded_to_conditional", False))
        for event in events:
            cluster_values = []
            for value in event.get("cluster_values", []) or []:
                try:
                    cluster_values.append(float(value))
                except Exception:
                    continue
            cluster_mean = float(np.mean(cluster_values)) if cluster_values else float("nan")
            rows.append(
                {
                    "Status": str(event.get("status", "")),
                    "Point": str(event.get("point_id", "")),
                    "Suspicious run": str(event.get("candidate_role", "")),
                    "Suspicious value": _fmt_repro_metric(event.get("candidate_value")),
                    "Extra run": str(event.get("repeat_role", "")),
                    "Extra value": _fmt_repro_metric(event.get("repeat_value")),
                    "Cluster runs": ", ".join(str(role) for role in (event.get("cluster_roles", []) or [])),
                    "Cluster mean": _fmt_repro_metric(cluster_mean),
                    "Downgraded": "Yes" if downgraded else "No",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def section_header(title: str, accent: str, background: str = "#f8fafc") -> None:
    st.markdown(
        f"""
        <div style="margin: 0.9rem 0 0.85rem 0;">
            <div style="
                height: 2px;
                border-radius: 999px;
                background: linear-gradient(90deg, {accent}66, transparent);
                margin-bottom: 0.45rem;
            "></div>
            <div style="
                padding: 0.62rem 0.85rem;
                border-radius: 12px;
                border: 1px solid {accent}44;
                border-left: 7px solid {accent};
                background: linear-gradient(100deg, {background}, #ffffff);
                box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
            ">
                <div style="font-weight: 700; color: #0f172a; letter-spacing: 0.2px;">{title}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


MULTI_SECTION_COLORS: dict[str, tuple[str, str]] = {
    "metadata": ("#0ea5e9", "#eff6ff"),
    "variables": ("#14b8a6", "#f0fdfa"),
    "settings": ("#f59e0b", "#fffbeb"),
    "repro": ("#ef4444", "#fef2f2"),
    "templates": ("#10b981", "#ecfdf5"),
    "reuse": ("#f97316", "#fff7ed"),
    "preview": ("#06b6d4", "#ecfeff"),
    "run": ("#22c55e", "#f0fdf4"),
}


def themed_section_header(section_key: str, title: str) -> None:
    accent, bg = MULTI_SECTION_COLORS.get(section_key, ("#64748b", "#f8fafc"))
    section_header(title, accent, bg)

# --- Page Title ---
st.title("Multi-Objective Optimization")
ui_mode_multi = st.radio(
    "UI Mode",
    options=["Quick", "Advanced"],
    horizontal=True,
    key="multi_ui_mode",
)
is_advanced_multi = ui_mode_multi == "Advanced"
if not is_advanced_multi:
    st.caption("Quick mode: core setup + run controls only. Switch to Advanced for reproducibility and design tooling.")

# --- Session defaults ---
if "simulation_mode" not in st.session_state:
    st.session_state.simulation_mode = "off"
if "opc_url" not in st.session_state:
    st.session_state.opc_url = "http://em-nun:57080"
if "use_autosampler" not in st.session_state:
    st.session_state.use_autosampler = False
if "volume_to_collect" not in st.session_state:
    st.session_state.volume_to_collect = 3.0
if "initial_experiments" not in st.session_state:
    st.session_state.initial_experiments = 5
if "total_iterations" not in st.session_state:
    st.session_state.total_iterations = 20
if "init_strategy" not in st.session_state:
    st.session_state.init_strategy = INIT_STRATEGY_OPTIONS[0]
if "random_seed" not in st.session_state:
    st.session_state.random_seed = 42
if "acq_func" not in st.session_state:
    st.session_state.acq_func = OPTIMIZER_ACQ_OPTIONS[0]
if "objectives" not in st.session_state:
    st.session_state.objectives = []
if "process_adapter" not in st.session_state:
    st.session_state.process_adapter = DEFAULT_PROCESS_ADAPTER
if "process_adapter_config" not in st.session_state:
    st.session_state.process_adapter_config = {}
if "running_protocol_script" not in st.session_state:
    st.session_state.running_protocol_script = None
if "measurement_source_prefix" not in st.session_state:
    st.session_state.measurement_source_prefix = "OpusOPCSvr.HP-CZC3484P17->"
if "measurement_source_signal" not in st.session_state:
    st.session_state.measurement_source_signal = "PDA - mM"
if "multi_ui_mode" not in st.session_state:
    st.session_state.multi_ui_mode = "Quick"
if "multi_repro_enabled" not in st.session_state:
    st.session_state.multi_repro_enabled = False
if "multi_repro_method" not in st.session_state:
    st.session_state.multi_repro_method = "LHS"
if "multi_repro_n_points" not in st.session_state:
    st.session_state.multi_repro_n_points = 8
if "multi_repro_patterns" not in st.session_state:
    st.session_state.multi_repro_patterns = list(REPRO_PATTERN_LABELS[:2])
if "multi_repro_cyclic_repeats" not in st.session_state:
    st.session_state.multi_repro_cyclic_repeats = 3
if "multi_repro_use_sentinel" not in st.session_state:
    st.session_state.multi_repro_use_sentinel = True
if "multi_repro_sentinel_every" not in st.session_state:
    st.session_state.multi_repro_sentinel_every = 5
if "multi_repro_escalate_cleaning" not in st.session_state:
    st.session_state.multi_repro_escalate_cleaning = True
if "multi_repro_max_cleaning" not in st.session_state:
    st.session_state.multi_repro_max_cleaning = 2
if "multi_repro_block_on_fail" not in st.session_state:
    st.session_state.multi_repro_block_on_fail = True
if "multi_repro_objective" not in st.session_state:
    st.session_state.multi_repro_objective = ""
if "multi_repro_outlier_repeat_enabled" not in st.session_state:
    st.session_state.multi_repro_outlier_repeat_enabled = False
if "multi_repro_outlier_pair_rsd_pct" not in st.session_state:
    st.session_state.multi_repro_outlier_pair_rsd_pct = 5.0
if "multi_repro_outlier_gap_pct" not in st.session_state:
    st.session_state.multi_repro_outlier_gap_pct = 10.0
if "multi_repro_outlier_downgrade_conditional" not in st.session_state:
    st.session_state.multi_repro_outlier_downgrade_conditional = True
if "multi_repro_report" not in st.session_state:
    st.session_state.multi_repro_report = None
if "multi_bo_qc_enabled" not in st.session_state:
    st.session_state.multi_bo_qc_enabled = False
if "multi_bo_qc_every_n_runs" not in st.session_state:
    st.session_state.multi_bo_qc_every_n_runs = 5
if "multi_bo_qc_count" not in st.session_state:
    st.session_state.multi_bo_qc_count = 0
if "multi_bo_qc_history" not in st.session_state:
    st.session_state.multi_bo_qc_history = []

# Apply deferred state updates before sidebar widgets are created.
_apply_multi_deferred_updates()

# One-shot sync from Reproducibility Studio when navigating via its buttons.
multi_repro_from_studio = st.session_state.pop("_multi_repro_from_studio", None)
if isinstance(multi_repro_from_studio, dict):
    for _k, _v in multi_repro_from_studio.items():
        st.session_state[_k] = _v

# Quick mode should not erase stored reproducibility settings.
# Gate execution remains tied to the effective flag below.
multi_repro_gate_enabled = bool(st.session_state.get("multi_repro_enabled", False)) and is_advanced_multi

# --- Sidebar Simulation Toggle and OPC URL ---
sim_mode_label = {
    "off": " Real Hardware (Full)",
    "hybrid": "Hybrid (Simulated Measurement)",
    "full": "Full Simulation (No Hardware)"
}
sim_modes = ["off", "hybrid", "full"]
sim_default = st.session_state.get("simulation_mode", "off")
if sim_default not in sim_modes:
    sim_default = "off"
simulation_mode = st.sidebar.selectbox(
    "Experiment Mode",
    options=sim_modes,
    index=sim_modes.index(sim_default),
    format_func=lambda x: sim_mode_label[x],
    key="multi_simulation_mode_select",
)
opc_url = st.sidebar.text_input("🔌 OPC Server URL", value=st.session_state.get("opc_url", "http://em-nun:57080"), key="multi_opc_url_input")
st.session_state.simulation_mode = simulation_mode
st.session_state.opc_url = opc_url

render_measurement_settings()

# --- Sidebar: Use Autosampler ---
use_autosampler = st.sidebar.checkbox("Use Autosampler", value=bool(st.session_state.get("use_autosampler", False)), key="multi_use_autosampler_input")
st.session_state.use_autosampler = use_autosampler

volume_to_collect = st.sidebar.number_input(
    "Desired volume (ml):",
    min_value=0.0,
    max_value=6.0,
    value=float(st.session_state.get("volume_to_collect", 3.0)),
    step=0.5,
    key="multi_volume_to_collect_input",
)
st.session_state.volume_to_collect = volume_to_collect

protocol_script_options = ["None"] + list_protocol_scripts()
protocol_script_default = st.session_state.get("running_protocol_script", "None") or "None"
if protocol_script_default not in protocol_script_options:
    protocol_script_default = "None"
selected_protocol_script = st.sidebar.selectbox(
    "Running Protocol File",
    options=protocol_script_options,
    index=protocol_script_options.index(protocol_script_default),
    key="multi_protocol_script_select",
)
st.session_state.running_protocol_script = None if selected_protocol_script == "None" else selected_protocol_script
if st.session_state.running_protocol_script:
    try:
        load_protocol_module(st.session_state.running_protocol_script)
        st.sidebar.caption(f"Using protocol: `{st.session_state.running_protocol_script}`")
    except Exception as exc:
        st.sidebar.error(f"Protocol load error: {exc}")
# --- Simulation Mode Banner ---
if st.session_state.simulation_mode != "off":
    st.warning("⚠️ Simulation Mode is ON — OPC hardware interaction is partially or fully disabled.")

# --- Resume Section ---
st.sidebar.markdown("---")
resume_file = st.sidebar.selectbox(
    "🔄 Resume from Previous Multi-Objective Run",
    options=["None"] + os.listdir(SAVE_DIR)
)
if resume_file != "None" and st.sidebar.button("Load Previous Run"):
    run_path = os.path.join(SAVE_DIR, resume_file)
    with open(os.path.join(run_path, "optimizer.pkl"), "rb") as f:
        st.session_state.optimizer = pickle.load(f)
    with open(os.path.join(run_path, "metadata.json"), "r") as f:
        metadata = json.load(f)
    df = read_campaign_data(os.path.join(run_path, "experiment_data.csv"), metadata["variables"])

    # Restore session state
    st.session_state.experiment_data = df.to_dict("records")
    st.session_state.iteration = len(df)
    st.session_state.variables = variables_as_tuples(metadata["variables"])
    restore_reference_categories("multi", metadata.get("reference_categories", {}))
    st.session_state.objectives = metadata["objectives"]
    st.session_state.initial_experiments = int(metadata.get("initial_experiments", st.session_state.get("initial_experiments", 5)))
    st.session_state.total_iterations = metadata["total_iterations"]
    st.session_state.init_strategy = metadata.get("init_strategy", st.session_state.get("init_strategy", "Random"))
    st.session_state.random_seed = metadata.get("random_seed", st.session_state.get("random_seed", 42))
    st.session_state.acq_func = metadata.get("acq_func", st.session_state.get("acq_func", "EI"))
    st.session_state.process_adapter = metadata.get("process_adapter", st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER))
    st.session_state.process_adapter_config = metadata.get("process_adapter_config", st.session_state.get("process_adapter_config", {}))
    st.session_state.running_protocol_script = metadata.get("running_protocol_script", st.session_state.get("running_protocol_script"))
    loaded_measurement_prefix = metadata.get(
        "measurement_source_prefix",
        st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
    )
    loaded_measurement_signal = metadata.get(
        "measurement_source_signal",
        st.session_state.get("measurement_source_signal", "PDA - mM"),
    )
    _queue_multi_deferred_updates(
        measurement_source_prefix=loaded_measurement_prefix,
        measurement_source_signal=loaded_measurement_signal,
        **measurement_session_updates(metadata),
    )
    st.session_state.multi_repro_report = metadata.get("reproducibility")
    st.session_state.multi_bo_qc_enabled = bool(metadata.get("bo_qc_sentinel_enabled", st.session_state.get("multi_bo_qc_enabled", False)))
    st.session_state.multi_bo_qc_every_n_runs = int(metadata.get("bo_qc_sentinel_every_n_runs", st.session_state.get("multi_bo_qc_every_n_runs", 5)))
    st.session_state.multi_bo_qc_count = int(metadata.get("bo_qc_sentinel_count", st.session_state.get("multi_bo_qc_count", 0)))
    st.session_state.multi_bo_qc_history = list(metadata.get("bo_qc_sentinel_history", st.session_state.get("multi_bo_qc_history", [])))
    st.session_state.optimization_running = True
    st.session_state.run_name = resume_file

    # --- Restore simulation_mode and opc_url from metadata ---
    st.session_state.simulation_mode = metadata.get("simulation_mode", "off")
    st.session_state.opc_url = metadata.get("opc_url", "http://em-nun:57080")

    # --- Re-initialize OPC client and runner ---
    st.session_state.opc_client = OPCClient(st.session_state.opc_url)
    st.session_state.optimization_running = False
    st.session_state.runner = replace_experiment_runner(
        st.session_state.get("runner"),
        st.session_state.opc_client,
        "multi_objective_log.csv",
        simulation_mode=st.session_state.simulation_mode,
        use_autosampler=st.session_state.use_autosampler,
        volume_to_collect=volume_to_collect,
        process_adapter=st.session_state.process_adapter,
        adapter_config=st.session_state.process_adapter_config,
        running_protocol_script=st.session_state.get("running_protocol_script"),
        measurement_source_prefix=loaded_measurement_prefix,
        measurement_source_signal=loaded_measurement_signal,
        **measurement_settings(metadata),
    )
    st.session_state.optimization_running = True

    st.success(f"Loaded run: {resume_file}")
    st.session_state.simulation_mode = metadata.get("simulation_mode", st.session_state.simulation_mode)
    st.session_state.opc_url = metadata.get("opc_url", st.session_state.opc_url)
    _queue_multi_deferred_updates(**widget_state_updates("multi", st.session_state))
    st.rerun()

# --- Experiment Metadata ---
themed_section_header("metadata", "Experiment Metadata")
experiment_name = st.text_input("Experiment Name", value=st.session_state.get("run_name", ""), key="multi_campaign_name")
experiment_date = st.date_input("Experiment Date", st.session_state.get("experiment_date", datetime.today()), key="multi_campaign_date")
experiment_notes = st.text_area("Additional Notes", value=st.session_state.get("experiment_notes", ""), key="multi_campaign_notes")
st.session_state.experiment_date = experiment_date
st.session_state.experiment_notes = experiment_notes

# --- Define Variables ---
themed_section_header("variables", "Optimization Variables")

render_variable_form()

# --- Optimization Settings ---
themed_section_header("settings", "Optimization Settings")
col5, col6 = st.columns(2)
init_exp_default = int(st.session_state.get("initial_experiments", 5))
total_default = int(st.session_state.get("total_iterations", 20))
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=init_exp_default, key="multi_initial_experiments_input")
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=total_default, key="multi_total_iterations_input")
st.session_state.initial_experiments = int(initial_experiments)
st.session_state.total_iterations = int(total_iterations)
init_options = INIT_STRATEGY_OPTIONS
init_default = st.session_state.get("init_strategy", init_options[0])
if init_default not in init_options:
    init_default = init_options[0]
seed_default = int(st.session_state.get("random_seed", 42))
acq_options = OPTIMIZER_ACQ_OPTIONS
acq_default = st.session_state.get("acq_func", acq_options[0])
if acq_default not in acq_options:
    acq_default = acq_options[0]
if is_advanced_multi:
    col7, col8, col9 = st.columns(3)
    init_strategy = col7.selectbox("Init Strategy", init_options, index=init_options.index(init_default), key="multi_init_strategy_select")
    random_seed = int(col8.number_input("Random Seed", min_value=0, max_value=2147483647, value=seed_default, step=1, key="multi_random_seed_input"))
    acq_func = col9.selectbox("Acquisition Function", acq_options, index=acq_options.index(acq_default), key="multi_acq_func_select")
else:
    init_strategy = init_default
    random_seed = seed_default
    acq_func = acq_default
st.session_state.init_strategy = init_strategy
st.session_state.random_seed = random_seed
st.session_state.acq_func = acq_func
objective_defaults = [obj for obj in st.session_state.get("objectives", []) if obj in MULTI_OBJECTIVE_OPTIONS]
objectives = st.multiselect("🎯 Select Objectives to Optimize", MULTI_OBJECTIVE_OPTIONS, default=objective_defaults, key="multi_objectives_select")
st.session_state.objectives = objectives

st.markdown("### Select Maximize or Minimize Objective")

objective_directions = {}
for obj in objectives:
    direction = st.selectbox(
        f"Direction for **{obj}**:",
        ["maximize", "minimize"],
        key=f"{obj}_direction"
    )
    objective_directions[obj] = direction

if is_advanced_multi:
    themed_section_header("repro", "Reproducibility")
    st.checkbox("Enable reproducibility gate before optimization start", key="multi_repro_enabled")
    if st.session_state.get("multi_repro_enabled", False):
        selected_metric = st.session_state.get("multi_repro_objective") or "not set"
        _multi_pattern_labels = set(st.session_state.get("multi_repro_patterns", []))
        _multi_patterns: list[ReplicatePattern] = []
        if "Immediate (A->A)" in _multi_pattern_labels or ReplicatePattern.IMMEDIATE.value in _multi_pattern_labels:
            _multi_patterns.append(ReplicatePattern.IMMEDIATE)
        if "Bracketed (A->B->A)" in _multi_pattern_labels or ReplicatePattern.BRACKETED.value in _multi_pattern_labels:
            _multi_patterns.append(ReplicatePattern.BRACKETED)
        if (
            REPRO_CYCLIC_LABEL in _multi_pattern_labels
            or REPRO_CYCLIC_LABEL_LEGACY in _multi_pattern_labels
            or ReplicatePattern.CYCLIC.value in _multi_pattern_labels
        ):
            _multi_patterns.append(ReplicatePattern.CYCLIC)
        _multi_use_sentinel = bool(st.session_state.get("multi_repro_use_sentinel", True))
        _multi_estimated_runs = _estimate_repro_schedule_runs(
            point_count=int(st.session_state.get("multi_repro_n_points", 8)),
            patterns=_multi_patterns or [ReplicatePattern.IMMEDIATE],
            sentinel_every_n_runs=int(st.session_state.get("multi_repro_sentinel_every", 5)) if _multi_use_sentinel else None,
            cyclic_repeats=int(st.session_state.get("multi_repro_cyclic_repeats", 3)),
        )
        st.caption(
            "Configured in Reproducibility Studio: "
            f"{st.session_state.get('multi_repro_method', 'LHS')}, "
            f"{int(st.session_state.get('multi_repro_n_points', 8))} points, "
            f"metric={selected_metric}, "
            f"cyclic repeats={int(st.session_state.get('multi_repro_cyclic_repeats', 3))}, "
            f"patterns={len(st.session_state.get('multi_repro_patterns', []))}, "
            f"sentinel={'on' if _multi_use_sentinel else 'off'}, "
            f"outlier repeat={'on' if st.session_state.get('multi_repro_outlier_repeat_enabled', False) else 'off'}, "
            f"estimated startup reproducibility runs={_multi_estimated_runs}."
        )
    if st.button("Open Reproducibility Studio", key="multi_open_repro_page"):
        st.session_state.selected_page = "reproducibility"
        st.rerun()
    existing_multi_repro_report = st.session_state.get("multi_repro_report")
    if isinstance(existing_multi_repro_report, dict):
        _render_repro_report_panel(existing_multi_repro_report)
else:
    st.caption("Reproducibility controls are hidden in Quick mode. Switch to Advanced to view status.")
if is_advanced_multi:
    # Reuse previous multi-objective campaigns
    themed_section_header("reuse", "Reuse Previous Campaigns (Init)")
    available_mo = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
    reuse_runs = st.multiselect("Select runs to reuse", options=available_mo)

    # Build and preview reusable data from selected runs
    if reuse_runs and objectives:
        if st.button("Load Previous Data"):
            curr_names = [n for n, *_ in st.session_state.variables]
            rows = []
            for run in reuse_runs:
                rpath = os.path.join(SAVE_DIR, run)
                meta_path = os.path.join(rpath, "metadata.json")
                data_path = os.path.join(rpath, "experiment_data.csv")
                if not (os.path.exists(meta_path) and os.path.exists(data_path)):
                    continue
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    prev_vars = variables_as_tuples(meta.get("variables", []))
                    prev_names = [n for n, *_ in prev_vars]
                    if prev_names != curr_names:
                        continue
                    df_prev = read_campaign_data(data_path, prev_vars)
                    # require all selected objectives
                    if not all(obj in df_prev.columns for obj in objectives):
                        continue
                    for idx, row in df_prev.iterrows():
                        try:
                            xvals = point_from_row(row, st.session_state.variables, allow_new_categories=True)
                            yvals = {obj: float(row[obj]) for obj in objectives}
                        except Exception:
                            continue
                        rec = {"Run": run, "Row": int(idx), "Select": True}
                        for name, val in zip(curr_names, xvals):
                            rec[name] = val
                        for obj in objectives:
                            rec[obj] = yvals[obj]
                        rows.append(rec)
                except Exception:
                    continue
            st.session_state.reuse_candidates_mo = pd.DataFrame(rows) if rows else pd.DataFrame([])

    if st.session_state.get("reuse_candidates_mo") is not None:
        df_show = st.session_state.reuse_candidates_mo.copy()
        if not df_show.empty:
            st.markdown("#### Select specific multi-objective data points to reuse")
            edited = st.data_editor(df_show, num_rows="fixed")
            st.session_state.reuse_candidates_mo = edited
            if st.button("Apply Selected As Initial Data"):
                curr_names = [n for n, *_ in st.session_state.variables]
                preloaded = []
                for _, r in edited.iterrows():
                    if not bool(r.get("Select", False)):
                        continue
                    try:
                        params = parameters_from_row(r, st.session_state.variables, allow_new_categories=True)
                        obj_vals = {obj: float(r[obj]) for obj in objectives}
                    except Exception:
                        continue
                    preloaded.append({
                        "params": params,
                        "objectives": obj_vals,
                        "source": f"Reused:{r.get('Run','')}#{int(r.get('Row',-1))}"
                    })
                st.session_state.preloaded_rows_mo = preloaded
                st.success(f"Prepared {len(preloaded)} initial data points. They will be attached on start.")
        else:
            st.info("No matching previous multi-objective data found for current variables/objectives.")

    # --- Preview Initial Design (reused + generated) before starting ---
    themed_section_header("preview", "Preview Initial Design")
    sort_options = ["Do not sort"]
    if st.session_state.variables:
        sort_options += [name for name, *_ in st.session_state.variables]
    col_sort, col_dir = st.columns([2, 1])
    with col_sort:
        st.selectbox(
            "Sort by variable",
            options=sort_options,
            key="initial_sort_by",
            index=0
        )
    with col_dir:
        st.radio(
            "Direction",
            options=["Ascending", "Descending"],
            key="initial_sort_direction",
            index=0
        )

    if st.button("Preview Initial Design (Before Start)"):
        try:
            curr_names = [n for n, *_ in st.session_state.variables]
            if not curr_names:
                st.warning("Add at least one variable to preview the design.")
            else:
                campaign_bounds = variable_domains(st.session_state.variables)
                rng = np.random.default_rng(random_seed)

                preview_reused = []
                reuse_df_preview = st.session_state.get("reuse_candidates_mo")
                if reuse_df_preview is not None and not reuse_df_preview.empty:
                    for _, r in reuse_df_preview.iterrows():
                        if not bool(r.get("Select", False)):
                            continue
                        try:
                            x = point_from_row(r, st.session_state.variables, allow_new_categories=True)
                        except Exception:
                            continue
                        preview_reused.append(x)

                reused_count = len(preview_reused)
                init_needed = max(0, int(initial_experiments) - reused_count)
                if init_strategy == "LHS":
                    gen_points = augmented_lhs_with_reuse(
                        campaign_bounds,
                        total_points=int(initial_experiments),
                        rng=rng,
                        reused_points=preview_reused,
                        trials=30,
                    )
                else:
                    gen_points = gap_aware_initial_points(
                        campaign_bounds,
                        init_needed,
                        rng,
                        existing_points=preview_reused,
                        method="Random",
                        pool_factor=30,
                    )

                rows_reused = [{**{n: v for n, v in zip(curr_names, x)}, "Source": "Reused"} for x in preview_reused]
                rows_generated = [{**{n: v for n, v in zip(curr_names, x)}, "Source": ("LHS" if init_strategy == "LHS" else "Random")} for x in gen_points]

                df_reused = pd.DataFrame(rows_reused)
                df_generated = pd.DataFrame(rows_generated)

                sort_choice = st.session_state.get("initial_sort_by", "Do not sort")
                sort_direction = st.session_state.get("initial_sort_direction", "Ascending")
                ascending = (sort_direction != "Descending")
                frames = [df_reused, df_generated]
                df_preview = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame([])
                if not df_preview.empty and sort_choice and sort_choice != "Do not sort" and sort_choice in df_preview.columns:
                    df_preview = df_preview.sort_values(
                        by=sort_choice,
                        ascending=ascending,
                        kind="mergesort"
                    ).reset_index(drop=True)
                if not df_preview.empty:
                    df_preview["Order"] = np.arange(1, len(df_preview) + 1)
                st.session_state.initial_design_preview = df_preview
                st.session_state.initial_design_preview_bounds = campaign_bounds
        except Exception as e:
            st.error(f"Failed to build preview: {e}")

    df_preview = st.session_state.get("initial_design_preview")
    if df_preview is not None and not df_preview.empty:
        reused_total = int((df_preview["Source"] == "Reused").sum()) if "Source" in df_preview.columns else 0
        st.info(f"Initial design preview: {len(df_preview)} points (Reused: {reused_total}, New: {len(df_preview) - reused_total})")
        st.dataframe(df_preview)

        bounds = st.session_state.get("initial_design_preview_bounds") or variable_domains(st.session_state.variables)
        var_cols = [n for n, *_ in st.session_state.variables]
        chart_rows = [st.columns(1) for _ in range((len(var_cols) + 1) // 1)]
        chart_placeholders = [col.empty() for row in chart_rows for col in row][:len(var_cols)]
        for idx, ((name, low, high, _, kind), placeholder) in enumerate(zip(st.session_state.variables, chart_placeholders)):
            try:
                chart = alt.Chart(df_preview).mark_tick(size=50, thickness=2).encode(
                    x=variable_axis((name, low, high, "", kind)),
                    color=alt.Color("Source:N", legend=alt.Legend(orient='top')),
                    tooltip=["Order:Q", "Source:N", name]
                ).properties(
                    height=300,
                    width=400,
                    title=alt.TitleParams(text=f"{name} initial spread", anchor="middle")
                )
                placeholder.altair_chart(chart, use_container_width=True)
            except Exception:
                pass

else:
    reuse_runs = []
    st.caption("Reuse and initial-design preview are hidden in Quick mode.")
themed_section_header("run", "Run Control")
st.markdown(
    "Would you like to use sentinel as a QC during optimization? "
    "This is an experiment that will be repeated every N optimization experiments."
)
qc_col1, qc_col2 = st.columns([2, 1])
qc_col1.checkbox("Use sentinel QC during optimization", key="multi_bo_qc_enabled")
qc_col2.number_input(
    "Repeat every N experiments",
    min_value=1,
    max_value=1000,
    step=1,
    key="multi_bo_qc_every_n_runs",
    disabled=not bool(st.session_state.get("multi_bo_qc_enabled", False)),
)
st.caption("QC sentinel runs are extra checks. They do not update the optimizer and do not count toward total iterations.")
render_reference_categories("multi")

render_campaign_setup("multi", experiment_name, experiment_date, experiment_notes)
col_actions_left, col_actions_right = st.columns([1, 1])
start_clicked = col_actions_left.button("▶️ Start Optimization")
stop_clicked = col_actions_right.button("🛑 Stop Optimization")
if stop_clicked:
    if st.session_state.get("optimization_running", False):
        st.session_state.stop_requested = True
        st.warning("Stop requested. The optimization will halt after the current iteration.")
    else:
        close_runner(st.session_state.get("runner"))
        st.info("No optimization is currently running.")
if start_clicked:
    if not st.session_state.variables:
        st.error("Define at least one optimization variable before starting.")
        close_runner(st.session_state.get("runner"))
        st.stop()

    if len(objectives) < 2:
        st.error("Please select at least two objectives to perform multi-objective optimization.")
    else:
        st.session_state.optimization_running = True
        st.session_state.iteration = 0
        st.session_state.experiment_data = []
        st.session_state.simulation_mode = simulation_mode
        st.session_state.opc_url = opc_url
        st.session_state.opc_client = OPCClient(st.session_state.opc_url)
        st.session_state.optimization_running = False
        st.session_state.runner = replace_experiment_runner(
            st.session_state.get("runner"),
            st.session_state.opc_client,
            "multi_objective_log.csv",
            simulation_mode=st.session_state.simulation_mode,
            use_autosampler=st.session_state.use_autosampler,
            volume_to_collect=volume_to_collect,
            process_adapter=st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            adapter_config=st.session_state.get("process_adapter_config", {}),
            running_protocol_script=st.session_state.get("running_protocol_script"),
            measurement_source_prefix=st.session_state.get("measurement_source_prefix"),
            measurement_source_signal=st.session_state.get("measurement_source_signal"),
            **measurement_settings(st.session_state),
        )
        st.session_state.optimization_running = True
        st.session_state.multi_repro_report = None
        st.session_state.multi_bo_qc_count = 0
        st.session_state.multi_bo_qc_history = []
        repro_seed_rows = []
        campaign_bounds = variable_domains(st.session_state.variables)
        curr_names = [n for n, *_ in st.session_state.variables]

        if multi_repro_gate_enabled:
            st.markdown("### Live Log")
            gate_log_placeholder = st.empty()
            sys.stdout = StreamlitLogger(placeholder=gate_log_placeholder)

            repro_objective = st.session_state.get("multi_repro_objective")
            if not repro_objective:
                st.error("Reproducibility gate is enabled, but no metric objective is selected.")
                st.session_state.optimization_running = False
                close_runner(st.session_state.get("runner"))
                st.stop()

            bounds_by_name = dict(zip((v[0] for v in st.session_state.variables), variable_domains(st.session_state.variables)))
            method_name = "lhs" if st.session_state.get("multi_repro_method", "LHS") == "LHS" else "corners_center"
            test_points = build_initialization_points(
                bounds=bounds_by_name,
                n_points=int(st.session_state.get("multi_repro_n_points", 8)),
                method=method_name,
                seed=int(random_seed),
            )
            sentinel_point = None
            if st.session_state.get("multi_repro_use_sentinel", True):
                sentinel_point = reference_point(st.session_state.variables, st.session_state.get("multi_reference_categories"))

            selected_patterns: list[ReplicatePattern] = []
            selected_pattern_labels = set(st.session_state.get("multi_repro_patterns", []))
            if "Immediate (A->A)" in selected_pattern_labels or ReplicatePattern.IMMEDIATE.value in selected_pattern_labels:
                selected_patterns.append(ReplicatePattern.IMMEDIATE)
            if "Bracketed (A->B->A)" in selected_pattern_labels or ReplicatePattern.BRACKETED.value in selected_pattern_labels:
                selected_patterns.append(ReplicatePattern.BRACKETED)
            if (
                REPRO_CYCLIC_LABEL in selected_pattern_labels
                or REPRO_CYCLIC_LABEL_LEGACY in selected_pattern_labels
                or ReplicatePattern.CYCLIC.value in selected_pattern_labels
            ):
                selected_patterns.append(ReplicatePattern.CYCLIC)

            if selected_patterns or sentinel_point is not None:
                patterns_to_run = selected_patterns or [ReplicatePattern.IMMEDIATE]
                cyclic_repeats = int(st.session_state.get("multi_repro_cyclic_repeats", 3))
                sentinel_every = int(st.session_state.get("multi_repro_sentinel_every", 5)) if sentinel_point else None
                adjudication_config = None
                if bool(st.session_state.get("multi_repro_outlier_repeat_enabled", False)):
                    adjudication_config = {
                        "pair_rsd_threshold_pct": float(st.session_state.get("multi_repro_outlier_pair_rsd_pct", 5.0)),
                        "outlier_gap_threshold_pct": float(st.session_state.get("multi_repro_outlier_gap_pct", 10.0)),
                        "max_extra_replicates_per_point": 1,
                        "downgrade_pass_to_conditional": bool(
                            st.session_state.get("multi_repro_outlier_downgrade_conditional", True)
                        ),
                    }
                startup_expected_runs = _estimate_repro_schedule_runs(
                    point_count=len(test_points),
                    patterns=patterns_to_run,
                    sentinel_every_n_runs=sentinel_every,
                    cyclic_repeats=cyclic_repeats,
                )
                retest_points = test_points[: min(3, len(test_points))]
                retest_expected_runs = _estimate_repro_schedule_runs(
                    point_count=len(retest_points),
                    patterns=patterns_to_run,
                    sentinel_every_n_runs=sentinel_every,
                    cyclic_repeats=cyclic_repeats,
                )

                st.markdown("#### Reproducibility Gate Monitor")
                repro_monitor_status = st.empty()
                repro_monitor_progress = st.progress(0.0)
                repro_monitor_table = st.empty()
                repro_runtime = {
                    "done": 0,
                    "planned": max(1, int(startup_expected_runs)),
                    "phase": "startup",
                }
                repro_rows = []
                repro_inter_run_delay_s = 1.0

                def _refresh_repro_monitor(note: str = "") -> None:
                    done = int(repro_runtime["done"])
                    planned = max(int(repro_runtime["planned"]), 1)
                    ratio = min(done / planned, 1.0)
                    phase = str(repro_runtime.get("phase", "startup"))
                    suffix = f" | {note}" if note else ""
                    repro_monitor_status.info(f"Reproducibility phase: `{phase}` | run `{done}` / `{planned}`{suffix}")
                    repro_monitor_progress.progress(ratio)
                    if repro_rows:
                        repro_monitor_table.dataframe(pd.DataFrame(repro_rows[-30:]), use_container_width=True)

                _refresh_repro_monitor("Preparing schedule")

                def _outlier_repeat_callback(info: dict) -> None:
                    repro_runtime["planned"] = int(repro_runtime["planned"]) + 1
                    point_id = str(info.get("point_id", ""))
                    note = f"Scheduling extra replicate for {point_id}" if point_id else "Scheduling extra replicate"
                    _refresh_repro_monitor(note)

                def _repro_multi_runner(point: dict, metadata: dict) -> dict:
                    repro_runtime["done"] += 1
                    run_no = int(repro_runtime["done"])
                    planned = max(int(repro_runtime["planned"]), run_no)
                    phase = str(metadata.get("phase", repro_runtime.get("phase", "startup")))
                    pattern = str(metadata.get("pattern", ""))
                    role = str(metadata.get("replicate_role", ""))
                    repro_runtime["phase"] = phase
                    param_row = dict(point)
                    row = {
                        "Run": run_no,
                        "Phase": phase,
                        "Pattern": pattern,
                        "Role": role,
                        "Status": "Running",
                        "Metric": np.nan,
                        **param_row,
                    }
                    repro_rows.append(row)
                    _refresh_repro_monitor(f"Running {pattern} {role}".strip())
                    if run_no > 1 and repro_inter_run_delay_s > 0:
                        time.sleep(repro_inter_run_delay_s)

                    try:
                        result = st.session_state.runner.run_experiment(
                            point,
                            experiment_number=run_no,
                            total_iterations=planned,
                            objectives=objectives,
                            directions=objective_directions,
                        )
                        metric_val = np.nan
                        if isinstance(result, dict):
                            raw = result.get(repro_objective)
                            if raw is None and result:
                                raw = next(iter(result.values()))
                            try:
                                metric_val = float(raw)
                            except Exception:
                                metric_val = np.nan
                        row["Status"] = "Done"
                        row["Metric"] = metric_val
                        _refresh_repro_monitor(f"Completed {pattern} {role}".strip())
                        return result
                    except Exception as exc:
                        row["Status"] = f"Failed: {exc}"
                        _refresh_repro_monitor(f"Failed {pattern} {role}".strip())
                        raise

                repro_engine = ReproducibilityEngine(_repro_multi_runner, objective_key=repro_objective)
                repro_engine.schedule_with_reproducibility(
                    test_points=test_points,
                    patterns=patterns_to_run,
                    cyclic_repeats=cyclic_repeats,
                    sentinel_point=sentinel_point,
                    sentinel_every_n_runs=sentinel_every,
                    base_metadata={
                        "workflow": "multi_objective",
                        "experiment_name": experiment_name,
                        "repro_objective": repro_objective,
                    },
                    phase="startup",
                )
                if adjudication_config:
                    repro_engine.adjudicate_cyclic_outliers(
                        base_metadata={
                            "workflow": "multi_objective",
                            "experiment_name": experiment_name,
                            "repro_objective": repro_objective,
                        },
                        repeat_callback=_outlier_repeat_callback,
                        **adjudication_config,
                    )
                repro_report = repro_engine.analyze()

                if st.session_state.get("multi_repro_escalate_cleaning", True):
                    needs_escalation = (
                        repro_report.get("decision") == DecisionLabel.FAIL or bool(repro_report.get("drift_detected"))
                    )
                    if needs_escalation:
                        def _cleaning_callback(_cleaning_level: int) -> None:
                            repro_runtime["phase"] = f"cleaning_{_cleaning_level}"
                            if retest_expected_runs > 0:
                                repro_runtime["planned"] = int(repro_runtime["planned"]) + int(retest_expected_runs)
                            _refresh_repro_monitor(f"Escalating cleaning to level {_cleaning_level}")
                            cleaner = getattr(st.session_state.runner, "cleaning_electrochemical_cell", None)
                            if callable(cleaner):
                                cleaner()

                        escalation = repro_engine.escalate_cleaning_and_retest(
                            selected_points=retest_points,
                            sentinel_point=sentinel_point,
                            sentinel_every_n_runs=sentinel_every,
                            patterns=patterns_to_run,
                            cyclic_repeats=cyclic_repeats,
                            max_cleaning_level=int(st.session_state.get("multi_repro_max_cleaning", 2)),
                            base_metadata={
                                "workflow": "multi_objective",
                                "experiment_name": experiment_name,
                                "repro_objective": repro_objective,
                            },
                            cleaning_callback=_cleaning_callback,
                            adjudication_config=adjudication_config,
                            adjudication_callback=_outlier_repeat_callback,
                        )
                        repro_report = escalation.get("final_analysis", repro_report)
                        repro_report["escalation"] = escalation

                repro_runtime["planned"] = max(int(repro_runtime["planned"]), int(repro_runtime["done"]))
                _refresh_repro_monitor("Completed")
                repro_monitor_progress.progress(1.0)

                run_name = experiment_name.strip() if experiment_name.strip() else "multiobjective_experiment"
                run_path = os.path.join(SAVE_DIR, run_name)
                os.makedirs(run_path, exist_ok=True)
                repro_log_path = os.path.join(run_path, "reproducibility_log.csv")
                repro_report_path = os.path.join(run_path, "reproducibility_report.json")
                repro_engine.save_csv_logs(repro_log_path)
                repro_report_safe = _json_compatible(repro_report)
                with open(repro_report_path, "w") as f:
                    json.dump(repro_report_safe, f, indent=2)
                st.session_state.multi_repro_report = repro_report_safe
                repro_seed_rows = _multi_repro_seed_rows(repro_engine, curr_names, objectives)

                decision_value = str(repro_report_safe.get("decision", "UNKNOWN"))
                if decision_value == DecisionLabel.PASS.value:
                    st.success("Reproducibility gate: PASS")
                elif decision_value == DecisionLabel.CONDITIONAL.value:
                    st.warning("Reproducibility gate: CONDITIONAL")
                else:
                    st.error("Reproducibility gate: FAIL")
                _render_repro_report_panel(repro_report_safe)
                if decision_value not in (DecisionLabel.PASS.value, DecisionLabel.CONDITIONAL.value):
                    if st.session_state.get("multi_repro_block_on_fail", True):
                        st.session_state.optimization_running = False
                        st.error("Optimization start blocked by reproducibility gate.")
                        close_runner(st.session_state.get("runner"))
                        st.stop()
            else:
                st.warning("Reproducibility gate enabled, but no replicate patterns or sentinel are configured. Gate skipped.")

        model_bounds = expanded_domains(st.session_state.variables, st.session_state.get("reuse_candidates_mo"))
        n_objectives = len(objectives)
        st.session_state.objectives = objectives  # <-- Always update objectives in session state
        # Build optimizer with model bounds (we manage initialization ourselves)
        optimizer_kwargs = {
            "dimensions": optimizer_dimensions(st.session_state.variables, model_bounds, backend="ProcessOptimizer"),
            "acq_optimizer": "sampling",
            "random_state": random_seed,
            "n_initial_points": 0,
            "n_objectives": n_objectives
        }
        try:
            st.session_state.optimizer = build_multiobjective_optimizer(acq_func=acq_func, **optimizer_kwargs)
        except TypeError as exc:
            if "acq_func" not in str(exc):
                raise
            st.session_state.optimizer = build_multiobjective_optimizer(**optimizer_kwargs)
            try:
                setattr(st.session_state.optimizer, "acq_func", acq_func)
            except Exception:
                pass
        st.session_state.stop_requested = False  # Reset stop flag

        # Seed reused data from applied selected rows and prepare initial queue
        rng = np.random.default_rng(random_seed)
        bounds = variable_domains(st.session_state.variables)
        curr_names = [n for n, *_ in st.session_state.variables]
        reused_count = 0
        existing_points = []
        preloaded = st.session_state.get("preloaded_rows_mo")
        seed_rows = []
        if preloaded:
            seed_rows.extend(preloaded)
        if repro_seed_rows:
            seed_rows.extend(repro_seed_rows)

        seen_seed_points = set()
        for rec in seed_rows:
            params = rec.get("params", {})
            obj_vals = rec.get("objectives", {})
            try:
                x = point_from_row(params, st.session_state.variables, allow_new_categories=True)
                y_multi = [-float(obj_vals[obj]) for obj in objectives]
            except Exception:
                continue
            if any(not np.isfinite(v) for v in y_multi):
                continue
            key = tuple(x)
            if key in seen_seed_points:
                continue
            seen_seed_points.add(key)

            st.session_state.optimizer.tell(x, y_multi)
            reused_count += 1
            existing_points.append(x)
            row = {
                "Experiment #": len(st.session_state.experiment_data) + 1,
                "Timestamp": "Reused",
                **params,
                **{obj: obj_vals.get(obj) for obj in objectives},
                "Source": rec.get("source", "Reused"),
            }
            st.session_state.experiment_data.append(row)
        st.session_state.iteration = len(st.session_state.experiment_data)
        if repro_seed_rows:
            st.info(f"Using {len(repro_seed_rows)} reproducibility seed point(s) for initialization.")

        init_needed = max(0, int(initial_experiments) - reused_count)
        if init_strategy == "LHS":
            init_points = augmented_lhs_with_reuse(
                bounds,
                total_points=int(initial_experiments),
                rng=rng,
                reused_points=existing_points,
                trials=30,
            )
        else:
            init_points = gap_aware_initial_points(
                bounds,
                init_needed,
                rng,
                existing_points=existing_points,
                method="Random",
                pool_factor=30,
            )
        sort_choice = st.session_state.get("initial_sort_by", "Do not sort")
        sort_direction = st.session_state.get("initial_sort_direction", "Ascending")
        if init_points and sort_choice and sort_choice != "Do not sort":
            curr_names = [n for n, *_ in st.session_state.variables]
            if sort_choice in curr_names:
                idx = curr_names.index(sort_choice)
                reverse = (sort_direction == "Descending")
                init_points = sorted(init_points, key=lambda x: x[idx], reverse=reverse)
        st.session_state.initial_queue = init_points
        st.session_state.reused_count = reused_count

# --- Optimization Loop ---
if st.session_state.get("optimization_running", False):
    # --- Stop Button ---
    if st.button("🛑 Stop Experiment"):
        st.session_state.stop_requested = True

    optimizer = st.session_state.optimizer
    experiment_data = st.session_state.experiment_data
    runner = st.session_state.runner
    iteration = st.session_state.iteration
    objectives = st.session_state.objectives
    bo_qc_enabled = bool(st.session_state.get("multi_bo_qc_enabled", False))
    bo_qc_every_n_runs = max(1, int(st.session_state.get("multi_bo_qc_every_n_runs", 5)))
    bo_qc_count = int(st.session_state.get("multi_bo_qc_count", 0))
    planned_qc_runs = (total_iterations // bo_qc_every_n_runs) if bo_qc_enabled else 0
    planned_total_live_runs = int(total_iterations + planned_qc_runs)
    qc_history = list(st.session_state.get("multi_bo_qc_history", []))
    bo_qc_point = reference_point(st.session_state.variables, st.session_state.get("multi_reference_categories"))

    st.markdown("### 📋 Optimization Log")
    # --- Live Logger Setup ---
    log_placeholder = st.empty()
    logger = StreamlitLogger(placeholder=log_placeholder)
    sys.stdout = logger 

    progress_bar = st.progress(iteration / total_iterations)
    st.markdown("### Pareto Chart")   
    pareto_chart_placeholder = st.empty()
    if bo_qc_enabled:
        st.markdown("### QC Sentinel Trend")
    qc_chart_placeholder = st.empty()

    def _render_multi_qc_chart() -> None:
        if not bo_qc_enabled:
            return
        if not qc_history:
            qc_chart_placeholder.info("QC sentinel chart will appear after the first sentinel run.")
            return
        df_qc = pd.DataFrame(qc_history)
        if df_qc.empty:
            qc_chart_placeholder.info("No QC sentinel values available yet.")
            return
        value_cols = [obj for obj in objectives if obj in df_qc.columns]
        if not value_cols:
            qc_chart_placeholder.info("No QC sentinel objective values available yet.")
            return
        df_long = df_qc.melt(
            id_vars=["qc_run", "after_experiment", "timestamp", "status"],
            value_vars=value_cols,
            var_name="objective",
            value_name="value",
        )
        df_long["value"] = pd.to_numeric(df_long["value"], errors="coerce")
        df_long = df_long[df_long["value"].notna()]
        if df_long.empty:
            qc_chart_placeholder.info("QC sentinel objective values are not available yet.")
            return
        qc_chart = alt.Chart(df_long).mark_line(point=True).encode(
            x=alt.X("qc_run:Q", title="QC run"),
            y=alt.Y("value:Q", title="Sentinel objective value"),
            color=alt.Color("objective:N", title="Objective"),
            tooltip=["qc_run:Q", "after_experiment:Q", "timestamp:N", "objective:N", "value:Q", "status:N"],
        ).properties(height=250)
        qc_chart_placeholder.altair_chart(qc_chart, use_container_width=True)

    _render_multi_qc_chart()

    with campaign_connections(runner):
        while iteration < total_iterations and st.session_state.get("optimization_running", False):
            if st.session_state.get("stop_requested", False):
                st.warning("Experiment stopped by user.")
                st.session_state.optimization_running = False
                st.session_state.stop_requested = False
                break

            # Use queued initial points first, else ask and clip to campaign bounds
            if st.session_state.get("initial_queue"):
                x = st.session_state.initial_queue.pop(0)
            else:
                x = optimizer.ask()
                # Clip to campaign bounds to enforce current limits
                campaign_bounds = variable_domains(st.session_state.variables)
                x = clip_point(x, campaign_bounds)
            params = {name: val for (name, *_), val in zip(st.session_state.variables, x)}
            run_serial = int(iteration + 1 + bo_qc_count)
            try:
                result = runner.run_experiment(
                    params,
                    experiment_number=run_serial,
                    total_iterations=total_iterations,
                    objectives=objectives,
                    directions=objective_directions,
                    status_title=f"Optimization Experiment {iteration + 1} of {total_iterations}",
                    status_note=f"Live run {run_serial} of ~{planned_total_live_runs} (includes QC runs).",
                )
            except Exception as exc:
                st.session_state.optimization_running = False
                st.error(f"Optimization run failed at iteration {iteration + 1}.")
                try:
                    runner.save_full_measurements_to_csv(experiment_name)
                except Exception as log_error:
                    print(f"Could not save failed measurement readings: {log_error}")
                st.exception(exc)
                break
            y_multi = [-result[obj] for obj in objectives]

            if not isinstance(y_multi, list) or len(y_multi) != len(objectives):
                st.error(f"Mismatch: expected {len(objectives)} objectives but got {len(y_multi)} in result.")
                close_runner(st.session_state.get("runner"))
                st.stop()

            optimizer.tell(x, y_multi)

            row = {
                "Experiment #": iteration + 1,
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                **params,
                **{f"{obj}": result[obj] for obj in objectives},
                **runner.measurement_result_fields(),
            }
            experiment_data.append(row)
            df_results = pd.DataFrame(experiment_data)
            raw_csv_path = runner.save_full_measurements_to_csv(experiment_name)

            # --- Update charts inside the loop ---

            if len(objectives) == 2 and not df_results.empty:
                obj_x, obj_y = objectives[0], objectives[1]
                df_results_plot = df_results.copy()
                for obj in objectives:
                    if st.session_state.get(f"{obj}_direction", "maximize") == "minimize":
                        df_results_plot[obj] = -df_results_plot[obj]
                df_pareto_calc = df_results.copy()
                for obj in objectives:
                    if st.session_state.get(f"{obj}_direction", "maximize") == "minimize":
                        df_pareto_calc[obj] = -df_pareto_calc[obj]

                pareto_df = df_pareto_calc[[obj_x, obj_y]].copy().sort_values(by=obj_x, ascending=False)
                pareto_front_indices = []
                best_so_far = -np.inf
                for idx, row_ in pareto_df.iterrows():
                    if row_[obj_y] > best_so_far:
                        pareto_front_indices.append(idx)
                        best_so_far = row_[obj_y]
                pareto_front_df = df_results_plot.loc[pareto_front_indices]

                x_vals = df_results_plot[obj_x]
                y_vals = df_results_plot[obj_y]
                x_range = x_vals.max() - x_vals.min()
                y_range = y_vals.max() - y_vals.min()
                x_buffer = x_range * 0.05 if x_range > 0 else 1
                y_buffer = y_range * 0.05 if y_range > 0 else 1
                x_min = x_vals.min() - x_buffer
                x_max = x_vals.max() + x_buffer
                y_min = y_vals.min() - y_buffer
                y_max = y_vals.max() + y_buffer

                chart = alt.Chart(df_results_plot).mark_circle(size=60).encode(
                    x=alt.X(f"{obj_x}:Q", title=obj_x, scale=alt.Scale(domain=[x_min, x_max])),
                    y=alt.Y(f"{obj_y}:Q", title=obj_y, scale=alt.Scale(domain=[y_min, y_max])),
                    tooltip=list(df_results.columns)).interactive()
                pareto_line = alt.Chart(pareto_front_df).mark_line(color="red").encode(
                    x=alt.X(f"{obj_x}:Q"), y=alt.Y(f"{obj_y}:Q"))

                pareto_chart_placeholder.altair_chart(chart + pareto_line, use_container_width=True)
            elif len(objectives) > 2:
                pareto_chart_placeholder.info("ℹ️ Pareto plot available only for 2 objectives at a time.")

            iteration += 1
            st.session_state.iteration = iteration
            st.session_state.experiment_data = experiment_data

            if bo_qc_enabled and iteration > 0 and iteration % bo_qc_every_n_runs == 0 and st.session_state.optimization_running:
                qc_serial = int(iteration + 1 + bo_qc_count)
                try:
                    qc_result = runner.run_experiment(
                        bo_qc_point,
                        experiment_number=qc_serial,
                        total_iterations=total_iterations,
                        objectives=objectives,
                        directions=objective_directions,
                        status_title=f"QC Sentinel {bo_qc_count + 1} (after optimization experiment {iteration})",
                        status_note=f"Live run {qc_serial} of ~{planned_total_live_runs}. This QC run does not update the optimizer.",
                    )
                    qc_row = {
                        "qc_run": int(bo_qc_count + 1),
                        "after_experiment": int(iteration),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "Done",
                    }
                    for obj in objectives:
                        raw_qc = None
                        if isinstance(qc_result, dict):
                            raw_qc = qc_result.get(obj)
                        try:
                            qc_row[obj] = float(raw_qc) if raw_qc is not None else np.nan
                        except Exception:
                            qc_row[obj] = np.nan
                    qc_history.append(qc_row)
                    bo_qc_count += 1
                    st.session_state.multi_bo_qc_count = bo_qc_count
                    st.session_state.multi_bo_qc_history = list(qc_history)
                except Exception as exc:
                    qc_row = {
                        "qc_run": int(bo_qc_count + 1),
                        "after_experiment": int(iteration),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": f"Failed: {exc}",
                    }
                    for obj in objectives:
                        qc_row[obj] = np.nan
                    qc_history.append(qc_row)
                    st.session_state.multi_bo_qc_history = list(qc_history)
                    st.warning(f"QC sentinel run failed after experiment {iteration}: {exc}")
                    st.session_state.optimization_running = False

            progress_bar.progress(iteration / total_iterations)
            _render_multi_qc_chart()
            time.sleep(0.5)

            # --- Save after each iteration ---
            run_name = experiment_name.strip() if experiment_name.strip() else "multiobjective_experiment"
            run_path = os.path.join(SAVE_DIR, run_name)
            os.makedirs(run_path, exist_ok=True)
            df_results.to_csv(os.path.join(run_path, "experiment_data.csv"), index=False)
            with open(os.path.join(run_path, "optimizer.pkl"), "wb") as f:
                pickle.dump(optimizer, f)
            metadata = {
                "variables": st.session_state.variables,
                "reference_categories": st.session_state.get("multi_reference_categories", {}),
                "objectives": objectives,
                "objective_directions": {obj: st.session_state.get(f"{obj}_direction", "maximize") for obj in objectives},
                "initial_experiments": int(initial_experiments),
                "total_iterations": total_iterations,
                "experiment_name": experiment_name,
                "experiment_notes": experiment_notes,
                "experiment_date": str(experiment_date),
                "simulation_mode": st.session_state.simulation_mode,
                "opc_url": st.session_state.opc_url,
                "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
                "process_adapter_config": st.session_state.get("process_adapter_config", {}),
                "running_protocol_script": st.session_state.get("running_protocol_script"),
                "measurement_source_prefix": st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
                "measurement_source_signal": st.session_state.get("measurement_source_signal", "PDA - mM"),
                "init_strategy": init_strategy,
                "random_seed": random_seed,
                "acq_func": acq_func,
                "reused_runs": reuse_runs,
                "reused_count": st.session_state.get("reused_count", 0),
                "reproducibility": st.session_state.get("multi_repro_report"),
                "bo_qc_sentinel_enabled": bool(bo_qc_enabled),
                "bo_qc_sentinel_every_n_runs": int(bo_qc_every_n_runs),
                "bo_qc_sentinel_count": int(bo_qc_count),
                "bo_qc_sentinel_history": list(qc_history),
                **measurement_settings(runner.__dict__),
            }
            with open(os.path.join(run_path, "metadata.json"), "w") as f:
                json.dump(metadata, f, indent=4)

    if iteration == total_iterations:
        st.success("✅ Multi-objective Optimization Complete!")
        st.session_state.optimization_running = False

        # Save results to CSV and metadata as before...
        df_results = pd.DataFrame(experiment_data)

        # --- Compute Pareto front for saving ---
        if len(objectives) == 2 and not df_results.empty:
            obj_x, obj_y = objectives[0], objectives[1]
            df_pareto_calc = df_results.copy()
            for obj in objectives:
                if st.session_state.get(f"{obj}_direction", "maximize") == "minimize":
                    df_pareto_calc[obj] = -df_pareto_calc[obj]
            pareto_df = df_pareto_calc[[obj_x, obj_y]].copy().sort_values(by=obj_x, ascending=False)
            pareto_front_indices = []
            best_so_far = -np.inf
            for idx, row_ in pareto_df.iterrows():
                if row_[obj_y] > best_so_far:
                    pareto_front_indices.append(idx)
                    best_so_far = row_[obj_y]
            pareto_front_df = df_results.loc[pareto_front_indices]
            best_result = pareto_front_df.to_dict("records")
        else:
            best_result = None

        optimization_settings = {
            "initial_experiments": initial_experiments,
            "total_iterations": total_iterations,
            "objectives": objectives,
            "method": "Bayesian Multi-Objective",
            "simulation_mode": st.session_state.simulation_mode,
            "opc_url": st.session_state.opc_url,
            "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            "running_protocol_script": st.session_state.get("running_protocol_script"),
            "measurement_source_prefix": st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
            "measurement_source_signal": st.session_state.get("measurement_source_signal", "PDA - mM"),
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "acq_func": acq_func,
            "reproducibility": st.session_state.get("multi_repro_report"),
            "bo_qc_sentinel_enabled": bool(st.session_state.get("multi_bo_qc_enabled", False)),
            "bo_qc_sentinel_every_n_runs": int(st.session_state.get("multi_bo_qc_every_n_runs", 5)),
            "bo_qc_sentinel_count": int(st.session_state.get("multi_bo_qc_count", 0)),
            **measurement_settings(runner.__dict__),
        }
        db_handler.save_experiment(
            name=experiment_name,
            notes=experiment_notes,
            variables=st.session_state.variables,
            df_results=df_results,
            best_result=best_result,
            settings=optimization_settings
        )
        st.info("All results and Pareto front saved to the database.")

    # Restore default stdout after optimization section.
    sys.stdout = sys.__stdout__

