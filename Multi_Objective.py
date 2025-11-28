import streamlit as st
from datetime import datetime
import numpy as np
import pandas as pd
import altair as alt
import time
from ProcessOptimizer import Optimizer
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import ExperimentRunner
from core.utils.logger import StreamlitLogger
import sys
import os
import json
import dill as pickle

# --- Save/Resume Section ---
SAVE_DIR = "resumable_multiobjective_runs"
os.makedirs(SAVE_DIR, exist_ok=True)

# --- Page Title ---
st.title("Multi-Objective Optimization")

# --- Sidebar Simulation Toggle and OPC URL ---
sim_mode_label = {
    "off": " Real Hardware (Full)",
    "hybrid": "Hybrid (Simulated Measurement)",
    "full": "Full Simulation (No Hardware)"
}
simulation_mode = st.sidebar.selectbox("Experiment Mode", options=["off", "hybrid", "full"], format_func=lambda x: sim_mode_label[x])
opc_url = st.sidebar.text_input("🔌 OPC Server URL", value="http://em-nun:57080")

# --- Sidebar: Use Autosampler ---
use_autosampler = st.sidebar.checkbox("Use Autosampler", value=True)
st.session_state.use_autosampler = use_autosampler

volume_to_collect = st.sidebar.number_input(
    "Desired volume (ml):",
    min_value=0.0,
    max_value=6.0,
    value=3.0,
    step=0.5
)

 
# --- Always initialize session state keys ---
if "simulation_mode" not in st.session_state:
    st.session_state.simulation_mode = simulation_mode
if "opc_url" not in st.session_state:
    st.session_state.opc_url = opc_url
if "init_strategy" not in st.session_state:
    st.session_state.init_strategy = "Random"
if "random_seed" not in st.session_state:
    st.session_state.random_seed = 42
if "acq_func" not in st.session_state:
    st.session_state.acq_func = "EI"

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
    df = pd.read_csv(os.path.join(run_path, "experiment_data.csv"))
    with open(os.path.join(run_path, "metadata.json"), "r") as f:
        metadata = json.load(f)

    # Restore session state
    st.session_state.experiment_data = df.to_dict("records")
    st.session_state.iteration = len(df)
    st.session_state.variables = metadata["variables"]
    st.session_state.objectives = metadata["objectives"]
    st.session_state.total_iterations = metadata["total_iterations"]
    st.session_state.init_strategy = metadata.get("init_strategy", st.session_state.get("init_strategy", "Random"))
    st.session_state.random_seed = metadata.get("random_seed", st.session_state.get("random_seed", 42))
    st.session_state.acq_func = metadata.get("acq_func", st.session_state.get("acq_func", "EI"))
    st.session_state.optimization_running = True
    st.session_state.run_name = resume_file

    # --- Restore simulation_mode and opc_url from metadata ---
    st.session_state.simulation_mode = metadata.get("simulation_mode", "off")
    st.session_state.opc_url = metadata.get("opc_url", "http://em-nun:57080")

    # --- Re-initialize OPC client and runner ---
    st.session_state.opc_client = OPCClient(st.session_state.opc_url)
    st.session_state.runner = ExperimentRunner(
        st.session_state.opc_client,
        "multi_objective_log.csv",
        simulation_mode=st.session_state.simulation_mode,
        use_autosampler=st.session_state.use_autosampler,
        volume_to_collect=volume_to_collect
    )

    st.success(f"Loaded run: {resume_file}")

# --- Experiment Metadata ---
st.subheader("🧪 Experiment Metadata")
experiment_name = st.text_input("Experiment Name", value=st.session_state.get("run_name", ""))
experiment_date = st.date_input("Experiment Date", datetime.today())
experiment_notes = st.text_area("Additional Notes")

# --- Define Variables ---
st.subheader("⚙️ Optimization Variables")

VARIABLE_OPTIONS = {
    "Temperature": "temperature",
    "Pressure": "pressure",
    "Ratio oraganic/aqueous": "ratio_org_aq",
    "Acid": "acid",
    "Residence Time": "residence_time",
    "Flow Rate": "flow_rate",
    "Base Concentration": "base_concentration",
    "Voltage": "Voltage",
    "Current": "Current",
}

if "variables" not in st.session_state:
    st.session_state.variables = []

with st.form(key="variable_form"):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        display_name = st.selectbox("Variable Name", list(VARIABLE_OPTIONS.keys()))
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
col5, col6 = st.columns(2)
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=5)
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=20)
init_options = ["Random", "LHS"]
init_default = st.session_state.get("init_strategy", init_options[0])
if init_default not in init_options:
    init_default = init_options[0]
seed_default = int(st.session_state.get("random_seed", 42))
acq_options = ["EI", "PI", "LCB", "gp_hedge"]
acq_default = st.session_state.get("acq_func", acq_options[0])
if acq_default not in acq_options:
    acq_default = acq_options[0]
col7, col8, col9 = st.columns(3)
init_strategy = col7.selectbox("Init Strategy", init_options, index=init_options.index(init_default))
st.session_state.init_strategy = init_strategy
random_seed = int(col8.number_input("Random Seed", min_value=0, max_value=2147483647, value=seed_default, step=1))
st.session_state.random_seed = random_seed
acq_func = col9.selectbox("Acquisition Function", acq_options, index=acq_options.index(acq_default))
st.session_state.acq_func = acq_func
OBJECTIVE_OPTIONS = [
    "Yield",
    "Normalized Area",
    "Throughput",
    "Used Organic",
    "Solvent Penalty",
    "Extraction Efficiency",
    "Space-Time Yield"
]
objectives = st.multiselect("🎯 Select Objectives to Optimize", OBJECTIVE_OPTIONS)

# --- Always keep objectives in session state ---
if "objectives" not in st.session_state or not st.session_state.objectives:
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

# Reuse previous multi-objective campaigns
st.markdown("### Reuse Previous Multi-Objective Campaigns (as init)")
available_mo = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
reuse_runs = st.multiselect("Select runs to reuse", options=available_mo)

def lhs_samples(bounds, n, rng):
    d = len(bounds)
    if d == 0 or n <= 0:
        return []
    lhs = np.zeros((n, d))
    for j in range(d):
        perm = rng.permutation(n)
        lhs[:, j] = (perm + rng.random(n)) / n
    for j, (low, high) in enumerate(bounds):
        lhs[:, j] = low + lhs[:, j] * (high - low)
    return [lhs[i, :].tolist() for i in range(n)]

def _normalize_points(points, bounds):
    norm = []
    for x in points:
        nx = []
        for (val, (low, high)) in zip(x, bounds):
            rng = (high - low)
            if rng > 0:
                nx.append((float(val) - low) / rng)
            else:
                nx.append(0.5)
        norm.append(nx)
    return np.array(norm) if norm else np.empty((0, len(bounds)))

def gap_aware_initial_points(bounds, n, rng, existing_points=None, method="LHS", pool_factor=10):
    """
    Generate n initial points that are well-spread relative to existing_points using farthest-point sampling
    from a larger pool (drawn via LHS or Random within campaign bounds).
    """
    if n <= 0:
        return []
    d = len(bounds)
    pool_n = max(n * max(2, int(pool_factor)), n)
    if method == "LHS":
        pool_real = lhs_samples(bounds, pool_n, rng)
    else:
        pool_real = [[rng.uniform(low, high) for (low, high) in bounds] for _ in range(pool_n)]
    pool_norm = _normalize_points(pool_real, bounds)

    existing_points = existing_points or []
    exist_norm = _normalize_points(existing_points, bounds)

    selected = []
    if pool_norm.size == 0:
        return selected
    if exist_norm.shape[0] > 0:
        min_d2 = np.full(pool_norm.shape[0], np.inf)
        for e in exist_norm:
            diff = pool_norm - e
            d2 = np.einsum("ij,ij->i", diff, diff)
            min_d2 = np.minimum(min_d2, d2)
    else:
        min_d2 = np.full(pool_norm.shape[0], np.inf)

    taken = np.zeros(pool_norm.shape[0], dtype=bool)
    for _ in range(n):
        avail = (~taken)
        if not np.any(avail):
            break
        idx = int(np.argmax(np.where(avail, min_d2, -1)))
        taken[idx] = True
        selected.append(pool_real[idx])
        p = pool_norm[idx]
        diff = pool_norm - p
        d2 = np.einsum("ij,ij->i", diff, diff)
        min_d2 = np.minimum(min_d2, d2)
    return selected

def augmented_lhs_with_reuse(bounds, total_points, rng, reused_points=None, trials=30):
    """
    Build a Latin hypercube of size `total_points` and align it to reused points,
    returning the remaining rows as new points. Chooses the best of several trials
    by maximizing the minimum pairwise distance in normalized space for the union
    (reused + new rows).
    """
    d = len(bounds)
    m = int(total_points)
    if m <= 0:
        return []
    reused_points = reused_points or []
    r = len(reused_points)
    if r >= m:
        return []

    def _lhs_unit(n):
        if n <= 0:
            return np.empty((0, d))
        M = np.zeros((n, d))
        for j in range(d):
            perm = rng.permutation(n)
            M[:, j] = (perm + rng.random(n)) / n
        return M

    def _to_real(U):
        R = np.zeros_like(U)
        for j, (low, high) in enumerate(bounds):
            R[:, j] = low + U[:, j] * (high - low)
        return R

    reused_norm = _normalize_points(reused_points, bounds)

    best_score = -np.inf
    best_new_real = []

    for _ in range(max(1, trials)):
        U = _lhs_unit(m)
        unused = set(range(m))
        for rp in reused_norm:
            if not unused:
                break
            un_idx = np.array(sorted(list(unused)))
            diff = U[un_idx] - rp
            d2 = np.einsum("ij,ij->i", diff, diff)
            j = int(un_idx[int(np.argmin(d2))])
            unused.remove(j)

        if not unused and r < m:
            continue
        new_rows_U = U[list(sorted(unused))]
        union = new_rows_U
        if reused_norm.shape[0] > 0:
            union = np.vstack([reused_norm, new_rows_U])
        if union.shape[0] > 1:
            mn = np.inf
            for i in range(union.shape[0]):
                d2 = np.einsum("ij,ij->i", (union - union[i]), (union - union[i]))
                d2[i] = np.inf
                mn = min(mn, float(np.min(d2)))
            score = mn
        else:
            score = 0.0

        if score > best_score:
            best_score = score
            best_new_real = _to_real(new_rows_U).tolist()

    return best_new_real

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
                prev_vars = meta.get("variables", [])
                prev_names = [n for n, *_ in prev_vars]
                if prev_names != curr_names:
                    continue
                df_prev = pd.read_csv(data_path)
                # require all selected objectives
                if not all(obj in df_prev.columns for obj in objectives):
                    continue
                for idx, row in df_prev.iterrows():
                    try:
                        xvals = [float(row[name]) for name in curr_names]
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
                    params = {name: float(r[name]) for name in curr_names}
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
        if not curr_names:
            st.warning("Add at least one variable to preview the design.")
        else:
            campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
            rng = np.random.default_rng(random_seed)

            preview_reused = []
            reuse_df_preview = st.session_state.get("reuse_candidates_mo")
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

    bounds = st.session_state.get("initial_design_preview_bounds") or [(low, high) for _, low, high, _ in st.session_state.variables]
    var_cols = [n for n, *_ in st.session_state.variables]
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
                tooltip=["Order:Q", "Source:N", name]
            ).properties(
                height=300,
                width=400,
                title=alt.TitleParams(text=f"{name} initial spread", anchor="middle")
            )
            placeholder.altair_chart(chart, use_container_width=True)
        except Exception:
            pass

col_actions_left, col_actions_right = st.columns([1, 1])
start_clicked = col_actions_left.button("▶️ Start Optimization")
stop_clicked = col_actions_right.button("🛑 Stop Optimization")
if stop_clicked:
    if st.session_state.get("optimization_running", False):
        st.session_state.stop_requested = True
        st.warning("Stop requested. The optimization will halt after the current iteration.")
    else:
        st.info("No optimization is currently running.")
if start_clicked:
    if len(objectives) < 2:
        st.error("Please select at least two objectives to perform multi-objective optimization.")
    else:
        st.session_state.optimization_running = True
        st.session_state.iteration = 0
        st.session_state.experiment_data = []
        st.session_state.simulation_mode = simulation_mode
        st.session_state.opc_url = opc_url
        st.session_state.opc_client = OPCClient(st.session_state.opc_url)
        st.session_state.runner = ExperimentRunner(st.session_state.opc_client, "multi_objective_log.csv", simulation_mode=st.session_state.simulation_mode, use_autosampler=st.session_state.use_autosampler, volume_to_collect=volume_to_collect)
        campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
        # Expand model bounds to include selected reuse rows
        model_bounds = campaign_bounds.copy()
        reuse_df_prev = st.session_state.get("reuse_candidates_mo")
        if reuse_df_prev is not None and not reuse_df_prev.empty:
            curr_names = [n for n, *_ in st.session_state.variables]
            for j, name in enumerate(curr_names):
                try:
                    mask = reuse_df_prev.get("Select", True)
                    col_vals = reuse_df_prev.loc[mask.astype(bool), name].astype(float)
                    if len(col_vals) > 0:
                        low_c, high_c = campaign_bounds[j]
                        model_low = float(min(low_c, col_vals.min()))
                        model_high = float(max(high_c, col_vals.max()))
                        model_bounds[j] = (model_low, model_high)
                except Exception:
                    pass
        n_objectives = len(objectives)
        st.session_state.objectives = objectives  # <-- Always update objectives in session state
        # Build optimizer with model bounds (we manage initialization ourselves)
        optimizer_kwargs = {
            "dimensions": model_bounds,
            "n_initial_points": 0,
            "n_objectives": n_objectives
        }
        try:
            st.session_state.optimizer = Optimizer(acq_func=acq_func, **optimizer_kwargs)
        except TypeError as exc:
            if "acq_func" not in str(exc):
                raise
            st.session_state.optimizer = Optimizer(**optimizer_kwargs)
            try:
                setattr(st.session_state.optimizer, "acq_func", acq_func)
            except Exception:
                pass
        st.session_state.stop_requested = False  # Reset stop flag

        # Seed reused data from applied selected rows and prepare initial queue
        rng = np.random.default_rng(random_seed)
        bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
        curr_names = [n for n, *_ in st.session_state.variables]
        reused_count = 0
        existing_points = []
        preloaded = st.session_state.get("preloaded_rows_mo")
        if preloaded:
            for rec in preloaded:
                params = rec.get("params", {})
                obj_vals = rec.get("objectives", {})
                try:
                    x = [float(params[name]) for name in curr_names]
                    y_multi = [-float(obj_vals[obj]) for obj in objectives]
                except Exception:
                    continue
                st.session_state.optimizer.tell(x, y_multi)
                reused_count += 1
                existing_points.append(x)
                # Append to current experiment_data as pre-existing rows
                row = {
                    "Experiment #": len(st.session_state.experiment_data) + 1,
                    "Timestamp": "Reused",
                    **params,
                    **{obj: obj_vals.get(obj) for obj in objectives},
                    "Source": rec.get("source", "Reused")
                }
                st.session_state.experiment_data.append(row)
            st.session_state.iteration = len(st.session_state.experiment_data)

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

    st.markdown("### 📋 Optimization Log")
    # --- Live Logger Setup ---
    log_placeholder = st.empty()
    logger = StreamlitLogger(placeholder=log_placeholder)
    sys.stdout = logger 

    progress_bar = st.progress(iteration / total_iterations)
    st.markdown("### Pareto Chart")   
    pareto_chart_placeholder = st.empty()

    while iteration < total_iterations:
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
            campaign_bounds = [(low, high) for _, low, high, _ in st.session_state.variables]
            x = [min(max(val, low), high) for val, (low, high) in zip(x, campaign_bounds)]
        params = {name: val for (name, *_), val in zip(st.session_state.variables, x)}
        result = runner.run_experiment(params, experiment_number=iteration + 1, total_iterations=total_iterations, objectives=objectives, directions=objective_directions)
        y_multi = [-result[obj] for obj in objectives]

        if not isinstance(y_multi, list) or len(y_multi) != len(objectives):
            st.error(f"Mismatch: expected {len(objectives)} objectives but got {len(y_multi)} in result.")
            st.stop()

        optimizer.tell(x, y_multi)

        row = {
            "Experiment #": iteration + 1,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **params,
            **{f"{obj}": result[obj] for obj in objectives}
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
        progress_bar.progress(iteration / total_iterations)
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
            "objectives": objectives,
            "objective_directions": {obj: st.session_state.get(f"{obj}_direction", "maximize") for obj in objectives},
            "total_iterations": total_iterations,
            "experiment_name": experiment_name,
            "experiment_notes": experiment_notes,
            "experiment_date": str(experiment_date),
            "simulation_mode": st.session_state.simulation_mode,
            "opc_url": st.session_state.opc_url,
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "acq_func": acq_func,
            "reused_runs": reuse_runs,
            "reused_count": st.session_state.get("reused_count", 0)
        }
        with open(os.path.join(run_path, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=4)

    if iteration == total_iterations:
        st.success("✅ Multi-objective Optimization Complete!")
        st.session_state.optimization_running = False

        # Save results to CSV and metadata as before...

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
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "acq_func": acq_func
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
