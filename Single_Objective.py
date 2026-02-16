import streamlit as st
from datetime import datetime
import numpy as np
import pandas as pd
import altair as alt
import time
import dill as pickle
import os
import json
from skopt.space import Real, Categorical  # <-- Add this import
from src.repro.planner import build_initialization_points
from src.repro.repro_engine import DecisionLabel, ReplicatePattern, ReproducibilityEngine
from core.optimization.bayesian_optimization import StepBayesianOptimizer
from core.optimization.initial_design import (
    augmented_lhs_with_reuse,
    gap_aware_initial_points,
)
from core.campaigns import (
    INIT_STRATEGY_OPTIONS,
    OPTIMIZER_ACQ_OPTIONS,
    SINGLE_OBJECTIVE_OPTIONS,
    VARIABLE_OPTIONS,
    build_single_campaign_template,
    list_campaign_templates,
    load_campaign_template,
    save_campaign_template,
    variables_as_tuples,
)
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import ExperimentRunner
from core.hardware.process_adapters import DEFAULT_PROCESS_ADAPTER
from core.hardware.protocol_scripts import (
    list_protocol_scripts,
    load_protocol_module,
)
from core.utils.logger import StreamlitLogger
import sys

SAVE_DIR = "resumable_runs"
os.makedirs(SAVE_DIR, exist_ok=True)

# --- Helpers ---
def sanitize_filename(name: str) -> str:
    """Make a safe folder/file name for Windows: strip trailing spaces/dots and replace invalid chars."""
    if not isinstance(name, str):
        name = str(name)
    # Strip whitespace at ends (Windows disallows trailing spaces and dots)
    cleaned = name.strip().rstrip(".")
    # Replace invalid characters
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        cleaned = cleaned.replace(ch, "_")
    # Collapse multiple spaces
    cleaned = " ".join(cleaned.split())
    # Fallback if empty
    return cleaned or "run"


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


def _single_repro_seed_rows(repro_engine, param_names):
    """Aggregate reproducibility runs into unique seed points for BO initialization."""
    aggregated = {}
    for record in getattr(repro_engine, "records", []):
        if bool(getattr(record.context, "is_sentinel", False)):
            continue
        if not bool(getattr(record.result, "success", False)):
            continue
        try:
            objective_value = float(record.result.objective)
        except Exception:
            continue
        if not np.isfinite(objective_value):
            continue

        try:
            params = {name: float(record.point.values[name]) for name in param_names}
        except Exception:
            continue

        key = tuple(params[name] for name in param_names)
        bucket = aggregated.setdefault(key, {"params": params, "values": []})
        bucket["values"].append(objective_value)

    rows = []
    for bucket in aggregated.values():
        values = [v for v in bucket["values"] if np.isfinite(v)]
        if not values:
            continue
        rows.append(
            {
                "params": bucket["params"],
                "response": float(np.mean(values)),
                "source": "Reproducibility",
            }
        )
    return rows


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


SINGLE_SECTION_COLORS: dict[str, tuple[str, str]] = {
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
    accent, bg = SINGLE_SECTION_COLORS.get(section_key, ("#64748b", "#f8fafc"))
    section_header(title, accent, bg)

# --- Page Title ---
st.title("🌟 Single Objective Optimization")
ui_mode_single = st.radio(
    "UI Mode",
    options=["Quick", "Advanced"],
    horizontal=True,
    key="single_ui_mode",
)
is_advanced_single = ui_mode_single == "Advanced"
if not is_advanced_single:
    st.caption("Quick mode: core setup + run controls only. Switch to Advanced for templates, reproducibility, and design tooling.")
    st.session_state.single_repro_enabled = False

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
if "response_to_optimize" not in st.session_state:
    st.session_state.response_to_optimize = SINGLE_OBJECTIVE_OPTIONS[0]
if "acq_func" not in st.session_state:
    st.session_state.acq_func = OPTIMIZER_ACQ_OPTIONS[0]
if "init_strategy" not in st.session_state:
    st.session_state.init_strategy = INIT_STRATEGY_OPTIONS[0]
if "random_seed" not in st.session_state:
    st.session_state.random_seed = 42
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
if "single_ui_mode" not in st.session_state:
    st.session_state.single_ui_mode = "Quick"
if "single_repro_enabled" not in st.session_state:
    st.session_state.single_repro_enabled = False
if "single_repro_method" not in st.session_state:
    st.session_state.single_repro_method = "LHS"
if "single_repro_n_points" not in st.session_state:
    st.session_state.single_repro_n_points = 8
if "single_repro_patterns" not in st.session_state:
    st.session_state.single_repro_patterns = ["Immediate (A->A)", "Bracketed (A->B->A)"]
if "single_repro_use_sentinel" not in st.session_state:
    st.session_state.single_repro_use_sentinel = True
if "single_repro_sentinel_every" not in st.session_state:
    st.session_state.single_repro_sentinel_every = 5
if "single_repro_escalate_cleaning" not in st.session_state:
    st.session_state.single_repro_escalate_cleaning = True
if "single_repro_max_cleaning" not in st.session_state:
    st.session_state.single_repro_max_cleaning = 2
if "single_repro_block_on_fail" not in st.session_state:
    st.session_state.single_repro_block_on_fail = True
if "single_repro_report" not in st.session_state:
    st.session_state.single_repro_report = None

# --- Sidebar Simulation Mode Selector ---
sim_mode_label = {
    "off": "🧪 Real Hardware (Full)",
    "hybrid": "🧪 Hybrid (Simulated Measurement)",
    "full": "🧪 Full Simulation (No Hardware)"
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
)
st.session_state.simulation_mode = simulation_mode

opc_url = st.sidebar.text_input("🔌 OPC Server URL", value=st.session_state.get("opc_url", "http://em-nun:57080"))
st.session_state.opc_url = opc_url

st.sidebar.markdown("Measurement Source")
measurement_source_prefix = st.sidebar.text_input(
    "Measurement OPC Prefix",
    key="measurement_source_prefix",
)
measurement_source_signal = st.sidebar.text_input(
    "Measurement Signal",
    key="measurement_source_signal",
)
st.sidebar.caption(f"Current measurement tag: `{measurement_source_prefix}{measurement_source_signal}`")

# --- Sidebar: Use Autosampler ---
use_autosampler = st.sidebar.checkbox("Use Autosampler", value=bool(st.session_state.get("use_autosampler", False)))
st.session_state.use_autosampler = use_autosampler

volume_to_collect = st.sidebar.number_input(
    "Desired volume (ml):",
    min_value=0.0,
    max_value=6.0,
    value=float(st.session_state.get("volume_to_collect", 3.0)),
    step=0.5
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
    key="single_protocol_script_select",
)
st.session_state.running_protocol_script = None if selected_protocol_script == "None" else selected_protocol_script
if st.session_state.running_protocol_script:
    try:
        load_protocol_module(st.session_state.running_protocol_script)
        st.sidebar.caption(f"Using protocol: `{st.session_state.running_protocol_script}`")
    except Exception as exc:
        st.sidebar.error(f"Protocol load error: {exc}")
if simulation_mode != "off":
    st.warning("⚠️ Simulation Mode is ON — OPC hardware interaction is partially or fully disabled.")
    

# --- Resume Section ---
st.sidebar.markdown("---")
resume_file = st.sidebar.selectbox("🔄 Resume from Previous Run", options=["None"] + os.listdir(SAVE_DIR))
if resume_file != "None" and st.sidebar.button("Load Previous Run"):
    run_path = os.path.join(SAVE_DIR, resume_file)
    with open(os.path.join(run_path, "optimizer.pkl"), "rb") as f:
        st.session_state.optimizer = pickle.load(f)
    df = pd.read_csv(os.path.join(run_path, "experiment_data.csv"))
    with open(os.path.join(run_path, "metadata.json"), "r") as f:
        metadata = json.load(f)

    st.session_state.experiment_data = df.to_dict("records")
    st.session_state.iteration = len(df)
    st.session_state.variables = metadata["variables"]
    st.session_state.response_to_optimize = metadata["response"]
    st.session_state.initial_experiments = int(metadata.get("initial_experiments", st.session_state.get("initial_experiments", 5)))
    st.session_state.total_iterations = metadata["total_iterations"]
    st.session_state.acq_func = metadata.get("acq_func", st.session_state.get("acq_func", OPTIMIZER_ACQ_OPTIONS[0]))
    st.session_state.init_strategy = metadata.get("init_strategy", st.session_state.get("init_strategy", INIT_STRATEGY_OPTIONS[0]))
    st.session_state.random_seed = int(metadata.get("random_seed", st.session_state.get("random_seed", 42)))
    st.session_state.process_adapter = metadata.get("process_adapter", st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER))
    st.session_state.process_adapter_config = metadata.get("process_adapter_config", st.session_state.get("process_adapter_config", {}))
    st.session_state.running_protocol_script = metadata.get("running_protocol_script", st.session_state.get("running_protocol_script"))
    st.session_state.measurement_source_prefix = metadata.get(
        "measurement_source_prefix",
        st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
    )
    st.session_state.measurement_source_signal = metadata.get(
        "measurement_source_signal",
        st.session_state.get("measurement_source_signal", "PDA - mM"),
    )
    st.session_state.single_repro_report = metadata.get("reproducibility")
    st.session_state.runner = ExperimentRunner(
        OPCClient(metadata["opc_url"]),
        "experiment_log.csv",
        simulation_mode=metadata["simulation_mode"],
        use_autosampler=st.session_state.use_autosampler,
        volume_to_collect=volume_to_collect,
        process_adapter=st.session_state.process_adapter,
        adapter_config=st.session_state.process_adapter_config,
        running_protocol_script=st.session_state.get("running_protocol_script"),
        measurement_source_prefix=st.session_state.get("measurement_source_prefix"),
        measurement_source_signal=st.session_state.get("measurement_source_signal"),
    )
    st.session_state.optimization_running = True
    st.session_state.run_name = resume_file
    
    # Load pending next parameters if they exist
    pending_params_file = os.path.join(run_path, "pending_next_params.json")
    if os.path.exists(pending_params_file):
        with open(pending_params_file, "r") as f:
            st.session_state.pending_next_params = json.load(f)
        st.info(f"📋 Loaded pending parameters for experiment {len(df) + 1}")
    else:
        st.session_state.pending_next_params = None

# --- Experiment Metadata ---
themed_section_header("metadata", "Experiment Metadata")
experiment_name = st.text_input("Experiment Name", value=st.session_state.get("run_name", datetime.now().strftime("run_%Y%m%d_%H%M%S")))
st.session_state.run_name = experiment_name
# Sanitize for filesystem safety and keep it consistent throughout the session
safe_name = sanitize_filename(st.session_state.run_name)
if safe_name != st.session_state.run_name:
    st.info(f"Adjusted run name to '{safe_name}' for filesystem compatibility.")
st.session_state.run_name = safe_name
experiment_name = safe_name
experiment_date = st.date_input("Experiment Date", datetime.today())
experiment_notes = st.text_area("Additional Notes")

# --- Define Variables ---
themed_section_header("variables", "Optimization Variables")

if "variables" not in st.session_state:
    st.session_state.variables = []

with st.form(key="variable_form"):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        variable_choices = list(VARIABLE_OPTIONS.keys()) + ["Custom..."]
        display_name = st.selectbox("Variable Name", variable_choices)
        if display_name == "Custom...":
            var_name = st.text_input("Custom Variable ID", value="", help="Use lowercase snake_case, e.g. cofeed_concentration")
        else:
            var_name = VARIABLE_OPTIONS[display_name]  # Use internal variable name
    with col2:
        lower_bound = st.number_input("Lower Bound", value=0.0, format="%.4f")
    with col3:
        upper_bound = st.number_input("Upper Bound", value=1.0, format="%.4f")
    with col4:
        unit = st.text_input("Unit")

    submitted = st.form_submit_button("Add Variable")
    if submitted:
        if var_name and lower_bound < upper_bound:
            st.session_state.variables.append((var_name, lower_bound, upper_bound, unit))
        else:
            st.warning("Please enter a valid name and ensure lower < upper bound.")

# --- Display Added Variables ---
if st.session_state.variables:
    st.markdown("### 📟 Added Variables")
    for i, (name, low, high, unit) in enumerate(st.session_state.variables):
        st.write(f"{i+1}. **{name}**: from {low} to {high} {unit}")
else:
    st.info("No variables added yet.")

# --- Optimization Settings ---
themed_section_header("settings", "Optimization Settings")
col5, col6, col7 = st.columns(3)
init_exp_default = int(st.session_state.get("initial_experiments", 5))
total_default = int(st.session_state.get("total_iterations", 20))
response_default = st.session_state.get("response_to_optimize", SINGLE_OBJECTIVE_OPTIONS[0])
if response_default not in SINGLE_OBJECTIVE_OPTIONS:
    response_default = SINGLE_OBJECTIVE_OPTIONS[0]
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=init_exp_default)
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=total_default)
response_to_optimize = col7.selectbox("Response to Optimize", SINGLE_OBJECTIVE_OPTIONS, index=SINGLE_OBJECTIVE_OPTIONS.index(response_default))
st.session_state.initial_experiments = int(initial_experiments)
st.session_state.total_iterations = total_iterations
st.session_state.response_to_optimize = response_to_optimize

# Additional BO settings
acq_default = st.session_state.get("acq_func", OPTIMIZER_ACQ_OPTIONS[0])
if acq_default not in OPTIMIZER_ACQ_OPTIONS:
    acq_default = OPTIMIZER_ACQ_OPTIONS[0]
init_default = st.session_state.get("init_strategy", INIT_STRATEGY_OPTIONS[0])
if init_default not in INIT_STRATEGY_OPTIONS:
    init_default = INIT_STRATEGY_OPTIONS[0]
seed_default = int(st.session_state.get("random_seed", 42))
if is_advanced_single:
    col_a, col_b, col_c = st.columns(3)
    acq_func = col_a.selectbox("Acquisition Function", OPTIMIZER_ACQ_OPTIONS, index=OPTIMIZER_ACQ_OPTIONS.index(acq_default))
    init_strategy = col_b.selectbox("Init Strategy", INIT_STRATEGY_OPTIONS, index=INIT_STRATEGY_OPTIONS.index(init_default))
    random_seed = int(col_c.number_input("Random Seed", min_value=0, max_value=2147483647, value=seed_default, step=1))
else:
    acq_func = acq_default
    init_strategy = init_default
    random_seed = seed_default
st.session_state.acq_func = acq_func
st.session_state.init_strategy = init_strategy
st.session_state.random_seed = random_seed

# Reproducibility (dedicated page workflow)
if is_advanced_single:
    themed_section_header("repro", "Reproducibility")
    st.checkbox("Enable reproducibility gate before optimization start", key="single_repro_enabled")
    if st.session_state.get("single_repro_enabled", False):
        st.caption(
            "Configured in Reproducibility Studio: "
            f"{st.session_state.get('single_repro_method', 'LHS')}, "
            f"{int(st.session_state.get('single_repro_n_points', 8))} points, "
            f"patterns={len(st.session_state.get('single_repro_patterns', []))}, "
            f"sentinel={'on' if st.session_state.get('single_repro_use_sentinel', True) else 'off'}."
        )
    col_repro_1, col_repro_2 = st.columns([1, 1])
    if col_repro_1.button("Open Reproducibility Studio", key="single_open_repro_page"):
        st.session_state.selected_page = "🧬 Reproducibility Studio"
        st.rerun()
    col_repro_2.caption("Detailed reproducibility settings moved to a dedicated page.")
else:
    st.caption("Reproducibility controls are hidden in Quick mode. Switch to Advanced to view status.")
if is_advanced_single:
    # Campaign templates
    themed_section_header("templates", "Campaign Templates")
    single_templates = list_campaign_templates(mode="single")
    selected_template = st.selectbox("Template", options=["None"] + single_templates, key="single_template_select")
    col_tpl_load, col_tpl_save = st.columns([1, 1])
    with col_tpl_load:
        if st.button("Load Template"):
            if selected_template == "None":
                st.warning("Please choose a template to load.")
            else:
                payload = load_campaign_template(selected_template)
                if not payload:
                    st.error("Failed to load template.")
                elif payload.get("mode") != "single":
                    st.error("Selected template is not a single-objective campaign.")
                else:
                    loaded_vars = variables_as_tuples(payload.get("variables", []))
                    if loaded_vars:
                        st.session_state.variables = loaded_vars
                    optimization = payload.get("optimization", {})
                    st.session_state.initial_experiments = int(optimization.get("initial_experiments", st.session_state.get("initial_experiments", 5)))
                    st.session_state.total_iterations = int(optimization.get("total_iterations", st.session_state.get("total_iterations", 20)))
                    loaded_response = optimization.get("response")
                    if loaded_response in SINGLE_OBJECTIVE_OPTIONS:
                        st.session_state.response_to_optimize = loaded_response
                    loaded_acq = optimization.get("acq_func")
                    if loaded_acq in OPTIMIZER_ACQ_OPTIONS:
                        st.session_state.acq_func = loaded_acq
                    loaded_init = optimization.get("init_strategy")
                    if loaded_init in INIT_STRATEGY_OPTIONS:
                        st.session_state.init_strategy = loaded_init
                    st.session_state.random_seed = int(optimization.get("random_seed", st.session_state.get("random_seed", 42)))
                    repro_cfg = optimization.get("reproducibility", {})
                    if isinstance(repro_cfg, dict):
                        st.session_state.single_repro_enabled = bool(repro_cfg.get("enabled", st.session_state.get("single_repro_enabled", False)))
                        st.session_state.single_repro_method = str(repro_cfg.get("method", st.session_state.get("single_repro_method", "LHS")))
                        st.session_state.single_repro_n_points = int(repro_cfg.get("n_points", st.session_state.get("single_repro_n_points", 8)))
                        st.session_state.single_repro_patterns = list(repro_cfg.get("patterns", st.session_state.get("single_repro_patterns", ["Immediate (A->A)", "Bracketed (A->B->A)"])))
                        st.session_state.single_repro_use_sentinel = bool(repro_cfg.get("use_sentinel", st.session_state.get("single_repro_use_sentinel", True)))
                        st.session_state.single_repro_sentinel_every = int(repro_cfg.get("sentinel_every_n_runs", st.session_state.get("single_repro_sentinel_every", 5)))
                        st.session_state.single_repro_escalate_cleaning = bool(repro_cfg.get("escalate_cleaning", st.session_state.get("single_repro_escalate_cleaning", True)))
                        st.session_state.single_repro_max_cleaning = int(repro_cfg.get("max_cleaning_level", st.session_state.get("single_repro_max_cleaning", 2)))
                        st.session_state.single_repro_block_on_fail = bool(repro_cfg.get("block_on_fail", st.session_state.get("single_repro_block_on_fail", True)))
                        for _widget_key in [
                            "single_repro_enabled_widget",
                            "single_repro_method_select",
                            "single_repro_n_points_input",
                            "single_repro_patterns_select",
                            "single_repro_use_sentinel_widget",
                            "single_repro_sentinel_every_input",
                            "single_repro_escalate_cleaning_widget",
                            "single_repro_max_cleaning_input",
                            "single_repro_block_on_fail_widget",
                        ]:
                            st.session_state.pop(_widget_key, None)
                    hardware = payload.get("hardware", {})
                    if hardware:
                        st.session_state.simulation_mode = hardware.get("simulation_mode", st.session_state.get("simulation_mode", "off"))
                        st.session_state.opc_url = hardware.get("opc_url", st.session_state.get("opc_url", "http://em-nun:57080"))
                        st.session_state.use_autosampler = bool(hardware.get("use_autosampler", st.session_state.get("use_autosampler", False)))
                        st.session_state.volume_to_collect = float(hardware.get("volume_to_collect", st.session_state.get("volume_to_collect", 3.0)))
                        st.session_state.process_adapter = hardware.get("process_adapter", st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER))
                        st.session_state.process_adapter_config = hardware.get("process_adapter_config", st.session_state.get("process_adapter_config", {}))
                        st.session_state.running_protocol_script = hardware.get("running_protocol_script", st.session_state.get("running_protocol_script"))
                        st.session_state.measurement_source_prefix = hardware.get(
                            "measurement_source_prefix",
                            st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
                        )
                        st.session_state.measurement_source_signal = hardware.get(
                            "measurement_source_signal",
                            st.session_state.get("measurement_source_signal", "PDA - mM"),
                        )
                    st.success(f"Loaded template: {selected_template}")
                    st.rerun()

    with col_tpl_save:
        save_template_name = st.text_input(
            "Template name",
            value=st.session_state.get("single_template_name", experiment_name),
            key="single_template_name",
        )
        if st.button("Save Current Template"):
            template_payload = build_single_campaign_template(
                template_name=save_template_name,
                variables=st.session_state.get("variables", []),
                optimization={
                    "initial_experiments": int(initial_experiments),
                    "total_iterations": int(total_iterations),
                    "response": response_to_optimize,
                    "acq_func": acq_func,
                    "init_strategy": init_strategy,
                    "random_seed": int(random_seed),
                    "reproducibility": {
                        "enabled": bool(st.session_state.get("single_repro_enabled", False)),
                        "method": st.session_state.get("single_repro_method", "LHS"),
                        "n_points": int(st.session_state.get("single_repro_n_points", 8)),
                        "patterns": list(st.session_state.get("single_repro_patterns", ["Immediate (A->A)", "Bracketed (A->B->A)"])),
                        "use_sentinel": bool(st.session_state.get("single_repro_use_sentinel", True)),
                        "sentinel_every_n_runs": int(st.session_state.get("single_repro_sentinel_every", 5)),
                        "escalate_cleaning": bool(st.session_state.get("single_repro_escalate_cleaning", True)),
                        "max_cleaning_level": int(st.session_state.get("single_repro_max_cleaning", 2)),
                        "block_on_fail": bool(st.session_state.get("single_repro_block_on_fail", True)),
                    },
                },
                hardware={
                    "simulation_mode": simulation_mode,
                    "opc_url": opc_url,
                    "use_autosampler": bool(st.session_state.get("use_autosampler", False)),
                    "volume_to_collect": float(st.session_state.get("volume_to_collect", 3.0)),
                    "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
                    "process_adapter_config": st.session_state.get("process_adapter_config", {}),
                    "running_protocol_script": st.session_state.get("running_protocol_script"),
                    "measurement_source_prefix": st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
                    "measurement_source_signal": st.session_state.get("measurement_source_signal", "PDA - mM"),
                },
            )
            path = save_campaign_template(save_template_name, template_payload)
            st.success(f"Template saved: {path}")

    # Reuse previous campaigns as initial data
    themed_section_header("reuse", "Reuse Previous Campaigns (Init)")
    available_runs = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
    reuse_runs = st.multiselect("Select previous runs to reuse", options=available_runs)

    # Build and preview reusable data from selected runs
    if reuse_runs:
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
                    prev_vars = meta.get("variables", [])
                    prev_resp = meta.get("response")
                    prev_names = [n for n, *_ in prev_vars]
                    if prev_names != curr_names or prev_resp != response_to_optimize:
                        continue
                    df_prev = pd.read_csv(data_path)
                    for idx, row in df_prev.iterrows():
                        try:
                            xvals = [float(row[name]) for name in curr_names]
                            yval = float(row.get(response_to_optimize, row.get("Measurement")))
                        except Exception:
                            continue
                        rec = {"Run": run, "Row": int(idx), "Select": True}
                        for name, val in zip(curr_names, xvals):
                            rec[name] = val
                        rec[response_to_optimize] = yval
                        rows.append(rec)
                except Exception:
                    continue
            if rows:
                st.session_state.reuse_candidates = pd.DataFrame(rows)
            else:
                st.session_state.reuse_candidates = pd.DataFrame([])

    if st.session_state.get("reuse_candidates") is not None:
        df_show = st.session_state.reuse_candidates.copy()
        if not df_show.empty:
            st.markdown("#### Select specific data points to reuse")
            edited = st.data_editor(df_show, num_rows="fixed")
            st.session_state.reuse_candidates = edited
            # Apply selection into a preload buffer for the campaign
            if st.button("Apply Selected As Initial Data"):
                curr_names = [n for n, *_ in st.session_state.variables]
                preloaded = []
                for _, r in edited.iterrows():
                    if not bool(r.get("Select", False)):
                        continue
                    try:
                        params = {name: float(r[name]) for name in curr_names}
                        resp_val = float(r.get(response_to_optimize))
                    except Exception:
                        continue
                    preloaded.append({
                        "params": params,
                        "response": resp_val,
                        "source": f"Reused:{r.get('Run','')}#{int(r.get('Row',-1))}"
                    })
                st.session_state.preloaded_rows_so = preloaded
                st.success(f"Prepared {len(preloaded)} initial data points. They will be attached on start.")

        else:
            st.info("No matching previous data found for current variables/response.")

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
            campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
            rng = np.random.default_rng(random_seed)

            # Collect selected reused points if available (no need to press Apply)
            preview_reused = []
            reuse_df_preview = st.session_state.get("reuse_candidates")
            if reuse_df_preview is not None and not reuse_df_preview.empty:
                for _, r in reuse_df_preview.iterrows():
                    if not bool(r.get("Select", False)):
                        continue
                    try:
                        x = [float(r[name]) for name in curr_names]
                    except Exception:
                        continue
                    preview_reused.append(x)

            reused_count = len(preview_reused)
            init_needed = max(0, int(initial_experiments) - reused_count)

            # Generate additional initial points (within campaign bounds)
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
                    pool_factor=30
                )

            # Build a preview dataframe
            rows_reused = []
            rows_generated = []
            for x in preview_reused:
                rows_reused.append({**{n: v for n, v in zip(curr_names, x)}, "Source": "Reused"})
            for x in gen_points:
                rows_generated.append({**{n: v for n, v in zip(curr_names, x)}, "Source": ("LHS" if init_strategy == "LHS" else "Random")})

            df_reused = pd.DataFrame(rows_reused)
            df_generated = pd.DataFrame(rows_generated)

            sort_choice = st.session_state.get("initial_sort_by", "Do not sort")
            sort_direction = st.session_state.get("initial_sort_direction", "Ascending")
            ascending = (sort_direction != "Descending")
            if not df_generated.empty and sort_choice and sort_choice != "Do not sort" and sort_choice in df_generated.columns:
                df_generated = df_generated.sort_values(
                    by=sort_choice,
                    ascending=ascending,
                    kind="mergesort"
                ).reset_index(drop=True)

            df_preview = pd.concat([df_reused, df_generated], ignore_index=True)
            if not df_preview.empty:
                df_preview["Order"] = np.arange(1, len(df_preview) + 1)

            st.session_state.initial_design_preview = df_preview
            st.session_state.initial_design_preview_bounds = campaign_bounds
        except Exception as e:
            st.error(f"Failed to build preview: {e}")

    # Render preview if available
    df_preview = st.session_state.get("initial_design_preview")
    if df_preview is not None and not df_preview.empty:
        st.info(f"Initial design preview: {len(df_preview)} points (Reused: {(df_preview['Source']=='Reused').sum()}, New: {(df_preview['Source']!='Reused').sum()})")
        st.dataframe(df_preview)

        # Per-variable spread plots
        bounds = st.session_state.get("initial_design_preview_bounds") or [(low, high) for _, low, high, _ in st.session_state.variables]
        var_cols = [n for n, *_ in st.session_state.variables]
        # Create a grid of small charts for each variable
        chart_rows = [st.columns(1) for _ in range((len(var_cols) + 1) // 1)]
        chart_placeholders = [col.empty() for row in chart_rows for col in row][:len(var_cols)]
        for idx, ((name, low, high, _), placeholder) in enumerate(zip(st.session_state.variables, chart_placeholders)):
            try:
                chart = alt.Chart(df_preview).mark_tick(size=50, thickness=2).encode(
                    x=alt.X(
                        f"{name}:Q",
                        scale=alt.Scale(domain=[low, high], nice=False),
                        axis=alt.Axis(title=name, tickCount=6, ticks=True, labels=True, grid=True, format=".3g")
                    ),
                    color=alt.Color("Source:N", legend=alt.Legend(orient='top')),
                    tooltip=["Order:Q", "Source:N"] + [name]
                ).properties(
                    height=300,
                    width=400,
                    title=alt.TitleParams(text=f"{name} initial spread", anchor="middle")
                )
                placeholder.altair_chart(chart, use_container_width=True)
            except Exception:
                # Best-effort plotting; skip if something goes wrong for a variable
                pass

else:
    reuse_runs = []
    st.caption("Templates, reuse, and initial-design preview are hidden in Quick mode.")
# --- Run & Stop Buttons ---
themed_section_header("run", "Run Control")
col_start, col_stop = st.columns(2)
if col_start.button("▶ Start Optimization"):
    run_path = os.path.join(SAVE_DIR, experiment_name)
    os.makedirs(run_path, exist_ok=True)
    # --- Build model bounds (union of campaign bounds and selected reuse rows) ---
    curr_names = [name for name, *_ in st.session_state.variables]
    campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
    model_bounds = campaign_bounds.copy()
    reuse_df_preview = st.session_state.get("reuse_candidates")
    if reuse_df_preview is not None and not reuse_df_preview.empty:
        for j, name in enumerate(curr_names):
            try:
                mask = reuse_df_preview.get("Select", True)
                col_vals = reuse_df_preview.loc[mask.astype(bool), name].astype(float)
                if len(col_vals) > 0:
                    low_c, high_c = campaign_bounds[j]
                    model_low = float(min(low_c, col_vals.min()))
                    model_high = float(max(high_c, col_vals.max()))
                    model_bounds[j] = (model_low, model_high)
            except Exception:
                pass
    # Create optimizer with model bounds; clip suggestions to campaign bounds
    opt_vars = [Real(lb, ub, name=name) for (name, _l, _u, _u2), (lb, ub) in zip(st.session_state.variables, model_bounds)]
    st.session_state.optimizer = StepBayesianOptimizer(opt_vars, acq_func=acq_func, random_state=random_seed, suggest_bounds=campaign_bounds)
    st.session_state.experiment_data = []
    st.session_state.iteration = 0
    st.session_state.runner = ExperimentRunner(
        OPCClient(opc_url),
        "experiment_log.csv",
        simulation_mode=simulation_mode,
        use_autosampler=st.session_state.use_autosampler,
        volume_to_collect=volume_to_collect,
        process_adapter=st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
        adapter_config=st.session_state.get("process_adapter_config", {}),
        running_protocol_script=st.session_state.get("running_protocol_script"),
        measurement_source_prefix=st.session_state.get("measurement_source_prefix"),
        measurement_source_signal=st.session_state.get("measurement_source_signal"),
    )
    st.session_state.optimization_running = True
    st.session_state.single_repro_report = None
    repro_seed_rows = []

    if st.session_state.get("single_repro_enabled", False):
        bounds_by_name = {name: (float(low), float(high)) for name, low, high, _ in st.session_state.variables}
        method_name = "lhs" if st.session_state.get("single_repro_method", "LHS") == "LHS" else "corners_center"
        test_points = build_initialization_points(
            bounds=bounds_by_name,
            n_points=int(st.session_state.get("single_repro_n_points", 8)),
            method=method_name,
            seed=int(random_seed),
        )
        sentinel_point = None
        if st.session_state.get("single_repro_use_sentinel", True):
            sentinel_point = {k: (low + high) / 2.0 for k, (low, high) in bounds_by_name.items()}

        selected_patterns: list[ReplicatePattern] = []
        selected_pattern_labels = set(st.session_state.get("single_repro_patterns", []))
        if "Immediate (A->A)" in selected_pattern_labels:
            selected_patterns.append(ReplicatePattern.IMMEDIATE)
        if "Bracketed (A->B->A)" in selected_pattern_labels:
            selected_patterns.append(ReplicatePattern.BRACKETED)

        if selected_patterns or sentinel_point is not None:
            def _repro_single_runner(point: dict, metadata: dict) -> dict:
                return st.session_state.runner.run_experiment(
                    point,
                    objectives=[response_to_optimize],
                )

            repro_engine = ReproducibilityEngine(_repro_single_runner, objective_key=response_to_optimize)
            repro_engine.schedule_with_reproducibility(
                test_points=test_points,
                patterns=selected_patterns or [ReplicatePattern.IMMEDIATE],
                sentinel_point=sentinel_point,
                sentinel_every_n_runs=int(st.session_state.get("single_repro_sentinel_every", 5)) if sentinel_point else None,
                base_metadata={
                    "workflow": "single_objective",
                    "experiment_name": experiment_name,
                },
                phase="startup",
            )
            repro_report = repro_engine.analyze()

            if st.session_state.get("single_repro_escalate_cleaning", True):
                needs_escalation = (
                    repro_report.get("decision") == DecisionLabel.FAIL or bool(repro_report.get("drift_detected"))
                )
                if needs_escalation:
                    def _cleaning_callback(_cleaning_level: int) -> None:
                        cleaner = getattr(st.session_state.runner, "cleaning_electrochemical_cell", None)
                        if callable(cleaner):
                            cleaner()

                    escalation = repro_engine.escalate_cleaning_and_retest(
                        selected_points=test_points[: min(3, len(test_points))],
                        sentinel_point=sentinel_point,
                        sentinel_every_n_runs=int(st.session_state.get("single_repro_sentinel_every", 5)) if sentinel_point else None,
                        patterns=selected_patterns or [ReplicatePattern.IMMEDIATE],
                        max_cleaning_level=int(st.session_state.get("single_repro_max_cleaning", 2)),
                        base_metadata={
                            "workflow": "single_objective",
                            "experiment_name": experiment_name,
                        },
                        cleaning_callback=_cleaning_callback,
                    )
                    repro_report = escalation.get("final_analysis", repro_report)
                    repro_report["escalation"] = escalation

            repro_log_path = os.path.join(run_path, "reproducibility_log.csv")
            repro_report_path = os.path.join(run_path, "reproducibility_report.json")
            repro_engine.save_csv_logs(repro_log_path)
            repro_report_safe = _json_compatible(repro_report)
            with open(repro_report_path, "w") as f:
                json.dump(repro_report_safe, f, indent=2)
            st.session_state.single_repro_report = repro_report_safe
            repro_seed_rows = _single_repro_seed_rows(repro_engine, curr_names)

            decision_value = str(repro_report_safe.get("decision", "UNKNOWN"))
            if decision_value == DecisionLabel.PASS.value:
                st.success("Reproducibility gate: PASS")
            elif decision_value == DecisionLabel.CONDITIONAL.value:
                st.warning("Reproducibility gate: CONDITIONAL")
            else:
                st.error("Reproducibility gate: FAIL")
                if st.session_state.get("single_repro_block_on_fail", True):
                    st.session_state.optimization_running = False
                    st.error("Optimization start blocked by reproducibility gate.")
                    st.stop()
        else:
            st.warning("Reproducibility gate enabled, but no replicate patterns or sentinel are configured. Gate skipped.")

    # Prepare reuse data and initial queue
    rng = np.random.default_rng(random_seed)
    bounds = campaign_bounds

    # Seed with preloaded rows and reproducibility points, then fill missing init points.
    reused_count = 0
    existing_points = []
    preloaded = st.session_state.get("preloaded_rows_so")
    seed_rows = []
    if preloaded:
        seed_rows.extend(preloaded)
    if repro_seed_rows:
        seed_rows.extend(repro_seed_rows)

    seen_seed_points = set()
    for rec in seed_rows:
        params = rec.get("params", {})
        try:
            x = [float(params[name]) for name in curr_names]
            y_val = float(rec.get("response"))
        except Exception:
            continue
        key = tuple(x)
        if key in seen_seed_points:
            continue
        seen_seed_points.add(key)
        if not np.isfinite(y_val):
            continue

        st.session_state.optimizer.observe(x, -y_val)
        reused_count += 1
        existing_points.append(x)

        row = {
            "Experiment #": len(st.session_state.experiment_data) + 1,
            "Timestamp": "Reused",
            **params,
            "Measurement": y_val,
            response_to_optimize: y_val,
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
            pool_factor=30
        )
    sort_choice = st.session_state.get("initial_sort_by", "Do not sort")
    sort_direction = st.session_state.get("initial_sort_direction", "Ascending")
    if sort_choice and sort_choice != "Do not sort" and init_points:
        var_names = [name for name, *_ in st.session_state.variables]
        if sort_choice in var_names:
            sort_index = var_names.index(sort_choice)
            init_points = sorted(
                init_points,
                key=lambda row: row[sort_index],
                reverse=(sort_direction == "Descending")
            )
    st.session_state.initial_queue = init_points
    st.session_state.reused_count = reused_count

# --- Edit results and refit optimizer ---
if st.session_state.get("experiment_data"):
    with st.expander("Edit Results and Refit Optimizer", expanded=False):
        try:
            df_current = pd.DataFrame(st.session_state.experiment_data)
            st.markdown("You can correct measured values below; applying will refit the optimizer to all current data.")
            edited_df = st.data_editor(df_current, num_rows="fixed")
            if st.button("Apply Edits & Refit"):
                # Ensure numeric types for variables and objective, sync Measurement with objective
                var_names = [name for name, *_ in st.session_state.variables]
                obj = st.session_state.response_to_optimize
                df_fixed = edited_df.copy()
                # Coerce numeric columns
                for col in var_names + [obj]:
                    if col in df_fixed.columns:
                        df_fixed[col] = pd.to_numeric(df_fixed[col], errors='coerce')
                # Sync Measurement to objective when present
                if "Measurement" in df_fixed.columns and obj in df_fixed.columns:
                    df_fixed["Measurement"] = df_fixed[obj]

                # Update session data
                st.session_state.experiment_data = df_fixed.to_dict(orient='records')
                st.session_state.iteration = len(st.session_state.experiment_data)

                # Rebuild optimizer from scratch and observe all edited rows
                campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
                opt_vars = [Real(lb, ub, name=name) for (name, _l, _u, _u2), (lb, ub) in zip(st.session_state.variables, campaign_bounds)]
                new_opt = StepBayesianOptimizer(opt_vars, acq_func=acq_func, random_state=random_seed, suggest_bounds=campaign_bounds)

                for row in df_fixed.itertuples(index=False):
                    try:
                        x = [float(getattr(row, name)) for name in var_names]
                        y = -float(getattr(row, obj))
                        new_opt.observe(x, y)
                    except Exception:
                        # Skip rows with missing data
                        continue

                st.session_state.optimizer = new_opt
                # Clear any initial queue to avoid replaying
                st.session_state.initial_queue = []

                # Persist edited results to disk if a run path is known
                run_name = st.session_state.get("run_name")
                if run_name:
                    run_path = os.path.join(SAVE_DIR, run_name)
                    try:
                        os.makedirs(run_path, exist_ok=True)
                        df_fixed.to_csv(os.path.join(run_path, "experiment_data.csv"), index=False)
                        with open(os.path.join(run_path, "optimizer.pkl"), "wb") as f:
                            pickle.dump(new_opt, f)
                    except Exception:
                        pass

                st.success("Applied edits and refit optimizer.")
        except Exception as e:
            st.error(f"Failed to render editor: {e}")

if col_stop.button("🛑 Stop Optimization"):
    st.session_state.optimization_running = False
    st.warning("🛑 Optimization manually stopped.")

# --- Optimization Loop ---
if st.session_state.get("optimization_running", False):
    log_placeholder = st.empty()
    logger = StreamlitLogger(placeholder=log_placeholder)
    sys.stdout = logger

    optimizer = st.session_state.optimizer
    experiment_data = st.session_state.experiment_data
    iteration = st.session_state.iteration
    runner = st.session_state.runner
    total_iterations = st.session_state.total_iterations
    response_to_optimize = st.session_state.response_to_optimize
    run_name = st.session_state.run_name
    run_path = os.path.join(SAVE_DIR, run_name)

    results_chart = st.empty()
    scatter_rows = [st.columns(2) for _ in range((len(st.session_state.variables) + 1) // 2)]
    scatter_placeholders = [col.empty() for row in scatter_rows for col in row][:len(st.session_state.variables)]

    while iteration < total_iterations and st.session_state.optimization_running:
        # Check if we have pending parameters from a previous resume
        if st.session_state.get("pending_next_params") is not None:
            x = st.session_state.pending_next_params
            st.session_state.pending_next_params = None  # Clear after use
        # Use any queued initial points next
        elif st.session_state.get("initial_queue"):
            x = st.session_state.initial_queue.pop(0)
        else:
            x = optimizer.suggest()
        
        params = {name: val for (name, *_), val in zip(st.session_state.variables, x)}
        result = runner.run_experiment(params, experiment_number=iteration + 1, total_iterations=total_iterations, objectives=[response_to_optimize])
        y = -result[response_to_optimize]
        optimizer.observe(x, y)

        row = {
            "Experiment #": iteration + 1,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **params,
            "Measurement": -y,
            response_to_optimize: result[response_to_optimize]
        }
        experiment_data.append(row)
        df_results = pd.DataFrame(experiment_data)
        raw_csv_path = runner.save_full_measurements_to_csv(experiment_name)

        os.makedirs(run_path, exist_ok=True)
        df_results.to_csv(os.path.join(run_path, "experiment_data.csv"), index=False)
        with open(os.path.join(run_path, "optimizer.pkl"), "wb") as f:
            pickle.dump(optimizer, f)
        metadata = {
            "variables": st.session_state.variables,
            "response": response_to_optimize,
            "initial_experiments": int(initial_experiments),
            "total_iterations": total_iterations,
            "opc_url": opc_url,
            "simulation_mode": simulation_mode,
            "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            "process_adapter_config": st.session_state.get("process_adapter_config", {}),
            "running_protocol_script": st.session_state.get("running_protocol_script"),
            "measurement_source_prefix": st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
            "measurement_source_signal": st.session_state.get("measurement_source_signal", "PDA - mM"),
            "acq_func": acq_func,
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "reused_runs": reuse_runs,
            "reused_count": st.session_state.get("reused_count", 0),
            "reproducibility": st.session_state.get("single_repro_report"),
        }
        with open(os.path.join(run_path, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=4)
        
        # Pre-generate and save next parameters for potential resume
        if iteration + 1 < total_iterations:
            if st.session_state.get("initial_queue"):
                next_x = st.session_state.initial_queue[0]  # Peek at next queued point
            else:
                next_x = optimizer.suggest()  # Generate next suggestion
            with open(os.path.join(run_path, "pending_next_params.json"), "w") as f:
                json.dump(next_x, f)
        else:
            # Remove pending params file if this is the last iteration
            pending_file = os.path.join(run_path, "pending_next_params.json")
            if os.path.exists(pending_file):
                os.remove(pending_file)

        # Progress line chart with non-zero baseline for better contrast
        y_vals = df_results[response_to_optimize]
        y_min = y_vals.min()
        y_max = y_vals.max()
        margin = (y_max - y_min) * 0.05 if y_max > y_min else 1
        line = alt.Chart(df_results).mark_line(point=True).encode(
            x=alt.X("Experiment #:Q"),
            y=alt.Y(f"{response_to_optimize}:Q", scale=alt.Scale(domain=[y_min - margin, y_max + margin])),
            tooltip=["Experiment #", response_to_optimize]
        ).properties(
            height=300,
            title=alt.TitleParams(text=f"{response_to_optimize} vs Experiment #", anchor="middle")
        )
        results_chart.altair_chart(line, use_container_width=True)

        for idx, (name, low, high, _) in enumerate(st.session_state.variables):
            df = df_results[[name, "Measurement"]]
            y_min = df["Measurement"].min()
            y_max = df["Measurement"].max()
            margin = (y_max - y_min) * 0.05 if y_max > y_min else 1
            chart = alt.Chart(df).mark_circle(size=60).encode(
                x=alt.X(f"{name}:Q", scale=alt.Scale(domain=[low, high])),
                y=alt.Y("Measurement:Q", scale=alt.Scale(domain=[y_min - margin, y_max + margin]))
            ).properties(
                height=350,
                title=alt.TitleParams(text=f"{name} vs Measurement", anchor="middle")
            )
            scatter_placeholders[idx].altair_chart(chart, use_container_width=True)

        iteration += 1
        st.session_state.iteration = iteration
        st.session_state.experiment_data = experiment_data
        time.sleep(1)

    if experiment_data and iteration == total_iterations:
        df_results = pd.DataFrame(experiment_data)
        st.success("✅ Optimization Complete!")
        best_row = df_results.loc[df_results["Measurement"].idxmax()]
        st.markdown("### 🥇 Best Result")
        st.write(best_row)

        export_to_csv(df_results, f"{run_name}_final_results.csv")


        optimization_settings = {
            "initial_experiments": initial_experiments,
            "total_iterations": total_iterations,
            "objective": response_to_optimize,
            "method": "Bayesian Single Objective",
            "simulation_mode": simulation_mode,
            "opc_url": opc_url,
            "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            "running_protocol_script": st.session_state.get("running_protocol_script"),
            "measurement_source_prefix": st.session_state.get("measurement_source_prefix", "OpusOPCSvr.HP-CZC3484P17->"),
            "measurement_source_signal": st.session_state.get("measurement_source_signal", "PDA - mM"),
            "reproducibility": st.session_state.get("single_repro_report"),
        }

        db_handler.save_experiment(
            name=experiment_name,
            notes=experiment_notes,
            variables=st.session_state.variables,
            df_results=df_results,
            best_result=best_row,
            settings=optimization_settings
        )

        st.session_state.optimization_running = False




















