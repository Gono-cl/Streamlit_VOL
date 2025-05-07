import streamlit as st
from datetime import datetime
import numpy as np
import pandas as pd
import altair as alt
import time
import dill as pickle
import os
import json
from core.optimization.bayesian_optimization import StepBayesianOptimizer
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import ExperimentRunner
from core.utils.logger import StreamlitLogger
import sys

SAVE_DIR = "resumable_runs"
os.makedirs(SAVE_DIR, exist_ok=True)

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
    st.session_state.runner = ExperimentRunner(OPCClient(metadata["opc_url"]), "experiment_log.csv", simulation_mode=metadata["simulation_mode"])
    st.session_state.optimization_running = True
    st.session_state.run_name = resume_file


# --- Experiment Metadata ---
st.subheader("🧪 Experiment Metadata")
experiment_name = st.text_input("Experiment Name", value=st.session_state.get("run_name", datetime.now().strftime("run_%Y%m%d_%H%M%S")))
st.session_state.run_name = experiment_name
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
col5, col6, col7 = st.columns(3)
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=5)
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=20)
response_to_optimize = col7.selectbox("Response to Optimize", ["Yield", "Conversion", "Transformation", "Productivity"])
st.session_state.total_iterations = total_iterations
st.session_state.response_to_optimize = response_to_optimize

# --- Run & Stop Buttons ---
col_start, col_stop = st.columns(2)
if col_start.button("▶ Start Optimization"):
    run_path = os.path.join(SAVE_DIR, experiment_name)
    os.makedirs(run_path, exist_ok=True)
    st.session_state.optimizer = StepBayesianOptimizer([
        (name, low, high) for name, low, high, _ in st.session_state.variables
    ])
    st.session_state.experiment_data = []
    st.session_state.iteration = 0
    st.session_state.runner = ExperimentRunner(OPCClient(opc_url), "experiment_log.csv", simulation_mode=simulation_mode)
    st.session_state.optimization_running = True

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
        x = optimizer.suggest()
        params = {name: val for (name, *_), val in zip(st.session_state.variables, x)}
        result = runner.run_experiment(params, experiment_number=iteration + 1, total_iterations=total_iterations, objectives=[response_to_optimize])
        y = -result[response_to_optimize]
        optimizer.observe(x, y)

        row = {
            "Experiment #": iteration + 1,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **params,
            "Measurement": -y
        }
        experiment_data.append(row)
        df_results = pd.DataFrame(experiment_data)

        os.makedirs(run_path, exist_ok=True)
        df_results.to_csv(os.path.join(run_path, "experiment_data.csv"), index=False)
        with open(os.path.join(run_path, "optimizer.pkl"), "wb") as f:
            pickle.dump(optimizer, f)
        metadata = {
            "variables": st.session_state.variables,
            "response": response_to_optimize,
            "total_iterations": total_iterations,
            "opc_url": opc_url,
            "simulation_mode": simulation_mode
        }
        with open(os.path.join(run_path, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=4)

        results_chart.line_chart(df_results[["Experiment #", "Measurement"]].set_index("Experiment #"))

        for idx, (name, low, high, _) in enumerate(st.session_state.variables):
            df = df_results[[name, "Measurement"]]
            chart = alt.Chart(df).mark_circle(size=60).encode(
                x=alt.X(f"{name}:Q", scale=alt.Scale(domain=[low, high])),
                y="Measurement:Q"
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




















    



