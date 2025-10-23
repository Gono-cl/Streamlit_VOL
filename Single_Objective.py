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
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import ExperimentRunner
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

# --- Sidebar Simulation Mode Selector ---
sim_mode_label = {
    "off": "🧪 Real Hardware (Full)",
    "hybrid": "🧪 Hybrid (Simulated Measurement)",
    "full": "🧪 Full Simulation (No Hardware)"
}
simulation_mode = st.sidebar.selectbox("Experiment Mode", options=["off", "hybrid", "full"], format_func=lambda x: sim_mode_label[x])
st.session_state.simulation_mode = simulation_mode

opc_url = st.sidebar.text_input("🔌 OPC Server URL", value="http://em-nun:57080")
st.session_state.opc_url = opc_url

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
    st.session_state.total_iterations = metadata["total_iterations"]
    st.session_state.runner = ExperimentRunner(OPCClient(metadata["opc_url"]), "experiment_log.csv", simulation_mode=metadata["simulation_mode"],use_autosampler=st.session_state.use_autosampler, volume_to_collect=volume_to_collect)
    st.session_state.optimization_running = True
    st.session_state.run_name = resume_file

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

VARIABLE_OPTIONS = {
    "Temperature": "temperature",
    "Pressure": "pressure",
    "Ratio oraganic/aqueous": "ratio_org_aq",
    "Acid": "acid",
    "Residence Time": "residence_time"
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
col5, col6, col7 = st.columns(3)
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=5)
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=20)
OBJECTIVE_OPTIONS = [
    "Yield",
    "Normalized Area",
    "Throughput",
    "Used Organic",
    "Solvent Penalty",
    "Extraction Efficiency",
    "Space-Time Yield"

]
response_to_optimize = col7.selectbox("Response to Optimize",OBJECTIVE_OPTIONS)
st.session_state.total_iterations = total_iterations
st.session_state.response_to_optimize = response_to_optimize

# Additional BO settings
col_a, col_b, col_c = st.columns(3)
acq_func = col_a.selectbox("Acquisition Function", ["EI", "PI", "LCB", "gp_hedge"], index=0)
init_strategy = col_b.selectbox("Init Strategy", ["Random", "LHS"], index=0)
random_seed = int(col_c.number_input("Random Seed", min_value=0, max_value=2147483647, value=42, step=1))

# Reuse previous campaigns as initial data
st.markdown("### Reuse Previous Campaigns (as init)")
available_runs = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
reuse_runs = st.multiselect("Select previous runs to reuse", options=available_runs)

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
    # Build candidate pool
    pool_n = max(n * max(2, int(pool_factor)), n)
    if method == "LHS":
        pool_real = lhs_samples(bounds, pool_n, rng)
    else:
        pool_real = [[rng.uniform(low, high) for (low, high) in bounds] for _ in range(pool_n)]
    pool_norm = _normalize_points(pool_real, bounds)

    # Normalize existing
    existing_points = existing_points or []
    exist_norm = _normalize_points(existing_points, bounds)

    selected = []
    selected_idx = []
    # Precompute min distance to existing for all candidates
    if exist_norm.shape[0] > 0:
        # pairwise distances to existing, then min over existing
        # (pool_norm is Mxd, exist_norm is Exd)
        # Compute efficient squared distances
        min_d2 = np.full(pool_norm.shape[0], np.inf)
        for e in exist_norm:
            diff = pool_norm - e
            d2 = np.einsum('ij,ij->i', diff, diff)
            min_d2 = np.minimum(min_d2, d2)
    else:
        min_d2 = np.full(pool_norm.shape[0], np.inf)

    taken = np.zeros(pool_norm.shape[0], dtype=bool)
    for _ in range(n):
        # pick index with maximum min distance
        # break if pool exhausted
        avail = (~taken)
        if not np.any(avail):
            break
        idx = int(np.argmax(np.where(avail, min_d2, -1)))
        taken[idx] = True
        selected_idx.append(idx)
        selected.append(pool_real[idx])
        # update min_d2 with the newly selected point
        p = pool_norm[idx]
        diff = pool_norm - p
        d2 = np.einsum('ij,ij->i', diff, diff)
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

    def _norm_real(X):
        return _normalize_points(X, bounds)

    reused_norm = _normalize_points(reused_points, bounds)

    best_score = -np.inf
    best_new_real = []

    for _ in range(max(1, trials)):
        U = _lhs_unit(m)  # m x d normalized LHS
        # Greedy assign: map each reused to a unique row in U (closest by L2)
        unused = set(range(m))
        assign = []  # (reused_idx, row_idx)
        for rp in reused_norm:
            if not unused:
                break
            un_idx = np.array(sorted(list(unused)))
            diff = U[un_idx] - rp
            d2 = np.einsum('ij,ij->i', diff, diff)
            j = int(un_idx[int(np.argmin(d2))])
            assign.append(j)
            unused.remove(j)

        # Remaining rows are candidates for new points
        if not unused and r < m:
            # all rows consumed; skip this trial
            continue
        new_rows_U = U[list(sorted(unused))]
        # Score = min pairwise distance of union(reused_norm, new_rows_U)
        union = new_rows_U
        if reused_norm.shape[0] > 0:
            union = np.vstack([reused_norm, new_rows_U])
        # compute pairwise distances and take min (avoid diag)
        if union.shape[0] > 1:
            # compute condensed min distance
            mn = np.inf
            for i in range(union.shape[0]):
                d2 = np.einsum('ij,ij->i', (union - union[i]), (union - union[i]))
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
        rows = []
        order = 1
        for x in preview_reused:
            rows.append({**{n: v for n, v in zip(curr_names, x)}, "Source": "Reused", "Order": order})
            order += 1
        for x in gen_points:
            rows.append({**{n: v for n, v in zip(curr_names, x)}, "Source": ("LHS" if init_strategy == "LHS" else "Random"), "Order": order})
            order += 1

        df_preview = pd.DataFrame(rows)
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
    st.session_state.runner = ExperimentRunner(OPCClient(opc_url), "experiment_log.csv", simulation_mode=simulation_mode, use_autosampler=st.session_state.use_autosampler, volume_to_collect=volume_to_collect)
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
    st.session_state.initial_queue = init_points
    st.session_state.reused_count = reused_count

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
        # Use any queued initial points first
        if st.session_state.get("initial_queue"):
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
            "total_iterations": total_iterations,
            "opc_url": opc_url,
            "simulation_mode": simulation_mode,
            "acq_func": acq_func,
            "init_strategy": init_strategy,
            "random_seed": random_seed,
            "reused_runs": reuse_runs,
            "reused_count": st.session_state.get("reused_count", 0)
        }
        with open(os.path.join(run_path, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=4)

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
        export_to_excel(df_results, f"{run_name}_final_results.xlsx")

        optimization_settings = {
            "initial_experiments": initial_experiments,
            "total_iterations": total_iterations,
            "objective": response_to_optimize,
            "method": "Bayesian Single Objective",
            "simulation_mode": simulation_mode,
            "opc_url": opc_url
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





















