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


# --- Page Title ---
st.title("🌈 Multi-Objective Optimization")

# --- Sidebar Simulation Toggle and OPC URL ---
sim_mode_label = {
    "off": "🧪 Real Hardware (Full)",
    "hybrid": "🧪 Hybrid (Simulated Measurement)",
    "full": "🧪 Full Simulation (No Hardware)"
}
simulation_mode = st.sidebar.selectbox("Experiment Mode", options=["off", "hybrid", "full"], format_func=lambda x: sim_mode_label[x])

opc_url = st.sidebar.text_input("🔌 OPC Server URL", value="http://em-nun:57080")

# --- Simulation Mode Banner ---
if simulation_mode != "off":
    st.warning("⚠️ Simulation Mode is ON — OPC hardware interaction is partially or fully disabled.")

# --- Experiment Metadata ---
st.subheader("🧪 Experiment Metadata")
experiment_name = st.text_input("Experiment Name")
experiment_date = st.date_input("Experiment Date", datetime.today())
experiment_notes = st.text_area("Additional Notes")

# --- Define Variables ---
st.subheader("⚙️ Optimization Variables")

if "variables" not in st.session_state:
    st.session_state.variables = []

with st.form(key="variable_form"):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        var_name = st.text_input("Variable Name")
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
OBJECTIVE_OPTIONS = [
    "Yield",
    "Normalized Area",
    "Throughput",
    "Used Organic",
    "Solvent Penalty",
    "Extraction Efficiency"
]
objectives = st.multiselect("🎯 Select Objectives to Optimize", OBJECTIVE_OPTIONS)

st.markdown("### Select Maximize or Minimize Objective")

objective_directions = {}
for obj in objectives:
    direction = st.selectbox(
        f"Direction for **{obj}**:",
        ["maximize", "minimize"],
        key=f"{obj}_direction"
    )
    objective_directions[obj] = direction

if st.button("Start Optimization"):
    if len(objectives) < 2:
        st.error("Please select at least two objectives to perform multi-objective optimization.")
    else:
        st.session_state.optimization_running = True
        st.session_state.iteration = 0
        st.session_state.experiment_data = []
        st.session_state.opc_client = OPCClient(opc_url)
        st.session_state.runner = ExperimentRunner(st.session_state.opc_client, "multi_objective_log.csv", simulation_mode=simulation_mode)
        search_space = [(low, high) for _, low, high, _ in st.session_state.variables]
        n_objectives = len(objectives)
        st.session_state.optimizer = Optimizer(
            dimensions=search_space,
            n_initial_points=initial_experiments,
            n_objectives=n_objectives
        )
    st.markdown("### 📋 Optimization Log")
    # --- Live Logger Setup ---
    log_placeholder = st.empty()
    logger = StreamlitLogger(placeholder=log_placeholder)
    sys.stdout = logger 

# --- Optimization Loop ---
if st.session_state.get("optimization_running", False):
    optimizer = st.session_state.optimizer
    experiment_data = st.session_state.experiment_data
    runner = st.session_state.runner
    iteration = st.session_state.iteration

    results_chart = st.empty()
    progress_bar = st.progress(iteration / total_iterations)

    while iteration < total_iterations:
        x = optimizer.ask()
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

        results_chart.line_chart(df_results[["Experiment #"] + objectives].set_index("Experiment #"))
        iteration += 1
        st.session_state.iteration = iteration
        st.session_state.experiment_data = experiment_data
        progress_bar.progress(iteration / total_iterations)
        time.sleep(0.5)

    if iteration == total_iterations:
        st.success("✅ Multi-objective Optimization Complete!")

        df_results_plot = df_results.copy()
        for obj in objectives:
            if objective_directions.get(obj) == "minimize":
                df_results_plot[obj] = -df_results_plot[obj]
        # 2. Flip values for Pareto front detection (maximize everything)
        df_pareto_calc = df_results.copy()
        for obj in objectives:
            if objective_directions.get(obj) == "minimize":
                df_pareto_calc[obj] = -df_pareto_calc[obj]

        # --- Pareto Plot (2D only for now) ---
        if len(objectives) == 2:
            obj_x, obj_y = objectives[0], objectives[1]
            st.markdown(f"### 🎯 Pareto Front: {obj_x} vs {obj_y}")

            
            pareto_df = df_pareto_calc[[obj_x, obj_y]].copy()
            pareto_df = pareto_df.sort_values(by=obj_x, ascending=False)

            pareto_front_indices = []
            best_so_far = -np.inf
            for idx, row in pareto_df.iterrows():
                if row[obj_y] > best_so_far:
                    pareto_front_indices.append(idx)
                    best_so_far = row[obj_y]
        
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

            if df_results_plot.empty:
                st.warning("⚠️ No data to plot. Try running some experiments first.")
            elif df_results_plot[[obj_x, obj_y]].dropna().empty:
                st.warning("⚠️ No valid values for selected objectives. Check if the objectives were computed correctly.")


            chart = alt.Chart(df_results_plot).mark_circle(size=60).encode(
                x=alt.X(f"{obj_x}:Q", title=obj_x, scale=alt.Scale(domain=[x_min, x_max])),
                y=alt.Y(f"{obj_y}:Q", title=obj_y, scale=alt.Scale(domain=[y_min, y_max])),
                tooltip=list(df_results.columns)).interactive()
            pareto_line = alt.Chart(pareto_front_df).mark_line(color="red").encode(
                x=alt.X(f"{obj_x}:Q"), y=alt.Y(f"{obj_y}:Q"))

            st.altair_chart(chart + pareto_line, use_container_width=True)
        else:
            st.info("ℹ️ Pareto plot available only for 2 objectives at a time.")

        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        export_to_csv(df_results, f"{timestamp}_multiobjective_results.csv")
        export_to_excel(df_results, f"{timestamp}_multiobjective_results.xlsx")

        raw_csv_path = runner.save_full_measurements_to_csv(experiment_name)


        optimization_settings = {
            "initial_experiments": initial_experiments,
            "total_iterations": total_iterations,
            "objectives": objectives,
            "method": "ProcessOptimizer",
            "simulation_mode": simulation_mode,
            "opc_url": opc_url,
            "raw_measurement_file": raw_csv_path
        }

        db_handler.save_experiment(
            name=experiment_name,
            notes=experiment_notes,
            variables=st.session_state.variables,
            df_results=df_results,
            best_result={},
            settings=optimization_settings
        )

        st.session_state.optimization_running = False


