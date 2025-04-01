import streamlit as st
from datetime import datetime
import numpy as np
import pandas as pd
import altair as alt
import time
from core.optimization.bayesian_optimization import StepBayesianOptimizer
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler
from core.hardware.opc_communication import OPCClient
from core.hardware.experimental_run import ExperimentRunner
from core.utils.logger import StreamlitLogger
import sys

# --- Page Title ---
st.title("🌟 Single Objective Optimization")

# --- Sidebar Simulation Toggle ---
simulation_mode = st.sidebar.checkbox("🧪 Simulation Mode", value=False)

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
col5, col6, col7 = st.columns(3)
initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=5)
total_iterations = col6.number_input("Total Iterations", min_value=1, max_value=100, value=20)
response_to_optimize = col7.selectbox("Response to Optimize", ["Yield", "Conversion", "Transformation", "Productivity"])

# --- Progress and Pause/Resume Controls ---
if "pause_optimization" not in st.session_state:
    st.session_state.pause_optimization = False
if "optimization_running" not in st.session_state:
    st.session_state.optimization_running = False

if st.button("Pause/Resume Optimization"):
    st.session_state.pause_optimization = not st.session_state.pause_optimization

# --- Run Optimization ---
if st.button("Start Optimization"):
    st.session_state.optimization_running = True
    st.session_state.optimizer = StepBayesianOptimizer([
        (name, low, high) for name, low, high, _ in st.session_state.variables
    ])
    st.session_state.experiment_data = []
    st.session_state.iteration = 0
    st.session_state.opc_client = OPCClient("http://em-nun:57080")
    st.session_state.runner = ExperimentRunner(st.session_state.opc_client, "experiment_log.csv", simulation_mode=simulation_mode)
    
    # --- Live Logger Setup ---
    log_placeholder = st.empty()
    logger = StreamlitLogger(placeholder=log_placeholder)
    sys.stdout = logger  # Redirect print() to Streamlit


# --- Optimization Loop ---
if st.session_state.get("optimization_running", False):
    optimizer = st.session_state.optimizer
    experiment_data = st.session_state.experiment_data
    iteration = st.session_state.iteration
    runner = st.session_state.runner

    results_chart = st.empty()
    progress_bar = st.progress(iteration / total_iterations)
    scatter_rows = [st.columns(2) for _ in range((len(st.session_state.variables) + 1) // 2)]
    scatter_placeholders = [col.empty() for row in scatter_rows for col in row][:len(st.session_state.variables)]

    while iteration < total_iterations:
        if st.session_state.pause_optimization:
            st.warning("⏸ Optimization paused. Press Resume to continue.")
            break

        x = optimizer.suggest()
        params = {name: val for (name, *_), val in zip(st.session_state.variables, x)}
        result = runner.run_experiment(params)
        y = -result[response_to_optimize]  # still minimizing
        optimizer.observe(x, y)

        row = {
            "Experiment #": iteration + 1,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **params,
            "Measurement": -y
        }
        experiment_data.append(row)
        df_results = pd.DataFrame(experiment_data)

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
        progress_bar.progress(iteration / total_iterations)
        time.sleep(0.5)

    if iteration == total_iterations:
        st.success("✅ Optimization Complete!")
        best_row = df_results.loc[df_results["Measurement"].idxmax()]
        st.markdown("### 🥇 Best Result")
        st.write(best_row)

        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        export_to_csv(df_results, f"{timestamp}_optimization_results.csv")
        export_to_excel(df_results, f"{timestamp}_optimization_results.xlsx")

        optimization_settings = {
            "initial_experiments": initial_experiments,
            "total_iterations": total_iterations,
            "objective": response_to_optimize,
            "method": "Bayesian (looped with pause/resume + hardware)",
            "simulation_mode": simulation_mode
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








    



