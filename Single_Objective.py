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
from core.hardware.process_adapters import (
    DEFAULT_PROCESS_ADAPTER,
    PROCESS_ADAPTER_LABELS,
    available_process_adapters,
)
from core.hardware.process_profiles import (
    list_process_profiles,
    load_process_profile,
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

# --- Page Title ---
st.title("🌟 Single Objective Optimization")

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

adapter_options = available_process_adapters()
adapter_default = st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER)
if adapter_default not in adapter_options:
    adapter_default = DEFAULT_PROCESS_ADAPTER if DEFAULT_PROCESS_ADAPTER in adapter_options else adapter_options[0]
process_adapter = st.sidebar.selectbox(
    "Process Adapter",
    options=adapter_options,
    index=adapter_options.index(adapter_default),
    format_func=lambda x: PROCESS_ADAPTER_LABELS.get(x, x),
)
st.session_state.process_adapter = process_adapter

profile_options = ["None"] + list_process_profiles()
profile_default = st.session_state.get("process_profile_name", "None")
if profile_default not in profile_options:
    profile_default = "None"
selected_profile = st.sidebar.selectbox(
    "Process Profile",
    options=profile_options,
    index=profile_options.index(profile_default),
    key="single_process_profile_select",
)
st.session_state.process_profile_name = selected_profile
if st.sidebar.button("Load Process Profile", key="single_load_process_profile"):
    if selected_profile == "None":
        st.warning("Select a process profile first.")
    else:
        payload = load_process_profile(selected_profile)
        if not payload:
            st.error("Could not load selected process profile.")
        else:
            st.session_state.process_adapter = payload.get("adapter", DEFAULT_PROCESS_ADAPTER)
            st.session_state.process_adapter_config = payload.get("adapter_config", {})
            st.success(f"Loaded process profile: {selected_profile}")
            st.rerun()

if st.sidebar.button("Open Process Builder", key="single_open_process_builder"):
    st.session_state.selected_page = "🧩 Process Builder"
    st.rerun()

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
    st.session_state.runner = ExperimentRunner(
        OPCClient(metadata["opc_url"]),
        "experiment_log.csv",
        simulation_mode=metadata["simulation_mode"],
        use_autosampler=st.session_state.use_autosampler,
        volume_to_collect=volume_to_collect,
        process_adapter=st.session_state.process_adapter,
        adapter_config=st.session_state.process_adapter_config,
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
st.subheader("🧪 Experiment Metadata")
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
st.subheader("⚙️ Optimization Variables")

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
st.subheader("⚙️ Optimization Settings")
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
col_a, col_b, col_c = st.columns(3)
acq_default = st.session_state.get("acq_func", OPTIMIZER_ACQ_OPTIONS[0])
if acq_default not in OPTIMIZER_ACQ_OPTIONS:
    acq_default = OPTIMIZER_ACQ_OPTIONS[0]
init_default = st.session_state.get("init_strategy", INIT_STRATEGY_OPTIONS[0])
if init_default not in INIT_STRATEGY_OPTIONS:
    init_default = INIT_STRATEGY_OPTIONS[0]
seed_default = int(st.session_state.get("random_seed", 42))
acq_func = col_a.selectbox("Acquisition Function", OPTIMIZER_ACQ_OPTIONS, index=OPTIMIZER_ACQ_OPTIONS.index(acq_default))
init_strategy = col_b.selectbox("Init Strategy", INIT_STRATEGY_OPTIONS, index=INIT_STRATEGY_OPTIONS.index(init_default))
random_seed = int(col_c.number_input("Random Seed", min_value=0, max_value=2147483647, value=seed_default, step=1))
st.session_state.acq_func = acq_func
st.session_state.init_strategy = init_strategy
st.session_state.random_seed = random_seed

# Campaign templates
st.markdown("### Campaign Templates")
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
                hardware = payload.get("hardware", {})
                if hardware:
                    st.session_state.simulation_mode = hardware.get("simulation_mode", st.session_state.get("simulation_mode", "off"))
                    st.session_state.opc_url = hardware.get("opc_url", st.session_state.get("opc_url", "http://em-nun:57080"))
                    st.session_state.use_autosampler = bool(hardware.get("use_autosampler", st.session_state.get("use_autosampler", False)))
                    st.session_state.volume_to_collect = float(hardware.get("volume_to_collect", st.session_state.get("volume_to_collect", 3.0)))
                    st.session_state.process_adapter = hardware.get("process_adapter", st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER))
                    st.session_state.process_adapter_config = hardware.get("process_adapter_config", st.session_state.get("process_adapter_config", {}))
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
            },
            hardware={
                "simulation_mode": simulation_mode,
                "opc_url": opc_url,
                "use_autosampler": bool(st.session_state.get("use_autosampler", False)),
                "volume_to_collect": float(st.session_state.get("volume_to_collect", 3.0)),
                "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
                "process_adapter_config": st.session_state.get("process_adapter_config", {}),
            },
        )
        path = save_campaign_template(save_template_name, template_payload)
        st.success(f"Template saved: {path}")

# Reuse previous campaigns as initial data
st.markdown("### Reuse Previous Campaigns (as init)")
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
st.markdown("#### Preview Initial Design")
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

# --- Run & Stop Buttons ---
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
    )
    st.session_state.optimization_running = True

    # Prepare reuse data and initial queue
    rng = np.random.default_rng(random_seed)
    bounds = campaign_bounds

    # If user applied preloaded initial data, attach them now to optimizer and experiment log
    reused_count = 0
    preloaded = st.session_state.get("preloaded_rows_so")
    if preloaded:
        curr_names = [n for n, *_ in st.session_state.variables]
        for rec in preloaded:
            params = rec.get("params", {})
            try:
                x = [float(params[name]) for name in curr_names]
                y = -float(rec.get("response"))
            except Exception:
                continue
            st.session_state.optimizer.observe(x, y)
            reused_count += 1
            # Add to experiment_data as an already completed row
            row = {
                "Experiment #": len(st.session_state.experiment_data) + 1,
                "Timestamp": "Reused",
                **params,
                "Measurement": -y,
                response_to_optimize: -y,
                "Source": rec.get("source", "Reused")
            }
            st.session_state.experiment_data.append(row)
        st.session_state.iteration = len(st.session_state.experiment_data)

    init_needed = max(0, int(initial_experiments) - reused_count)
    # Build existing points from reused preloaded selections for gap-aware fill
    existing_points = []
    if preloaded:
        for rec in preloaded:
            try:
                x = [float(rec["params"][name]) for name, *_ in st.session_state.variables]
                existing_points.append(x)
            except Exception:
                pass
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
            "acq_func": acq_func,
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "reused_runs": reuse_runs,
            "reused_count": st.session_state.get("reused_count", 0)
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



















