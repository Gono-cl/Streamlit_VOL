import streamlit as st
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.figure import Figure 
import numpy as np
import pandas as pd
import altair as alt
import time
from core.optimization.bayesian_optimization import BayesianOptimizer

# --- Page Title ---
st.title("🎯 Single Objective Optimization")

# --- Section: Experiment Metadata ---
st.subheader("🧪 Experiment Metadata")
experiment_name = st.text_input("Experiment Name")
experiment_date = st.date_input("Experiment Date", datetime.today())
experiment_notes = st.text_area("Additional Notes")

# --- Section: Define Variables ---
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
    st.markdown("### 🧾 Added Variables")
    for i, (name, low, high, unit) in enumerate(st.session_state.variables):
        st.write(f"{i+1}. **{name}**: from {low} to {high} {unit}")
else:
    st.info("No variables added yet.")

# --- Optimization Settings ---
st.subheader("⚙️ Optimization Settings")
col5, col6, col7 = st.columns(3)

initial_experiments = col5.number_input("Initialization Experiments", min_value=1, max_value=100, value=5)
iterations = col6.number_input("Number of Iterations", min_value=1, max_value=100, value=20)
response_to_optimize = col7.selectbox("Response to Optimize", ["Yield", "Conversion", "Transformation", "Productivity"])


# Button to start optimization
if st.button("Start Optimization"):
    st.success("Optimization Started")
    results_chart_container = st.empty()
    scatter_chart_containers = st.columns(len(st.session_state.variables))
    scatter_placeholders = [col.empty() for col in scatter_chart_containers]

    # Initialize data storage
    results = []
    variable_data = {name: [] for name, *_ in st.session_state.variables}

    # Run Optimization loop
    for i in range(iterations):
        x = [
            np.random.uniform(low, high)
            for _, low, high, _ in st.session_state.variables
        ]
        y = -np.sum(x)  # Replace with real objective function
        results.append(-y)

        for idx, (name, *_rest) in enumerate(st.session_state.variables):
            variable_data[name].append(x[idx])

        # Line chart
        df_results = pd.DataFrame({"Measurement": results})
        df_results.index = df_results.index + 1
        results_chart_container.line_chart(df_results, x_label="Experiment", y_label="Measurement")

        # Scatter plots
        for idx, container in enumerate(scatter_placeholders):
            name, low, high, _ = st.session_state.variables[idx]
            df = pd.DataFrame({name: variable_data[name], "Measurement": results})
            chart = alt.Chart(df).mark_circle(size=60).encode(
                x=alt.X(f"{name}:Q", scale=alt.Scale(domain=[low, high])),
                y="Measurement:Q"
            ).properties(
                width=400,
                height=350,
                title=f"{name} vs Measurement"
            ).configure_title(
                anchor='middle'
            )
            container.altair_chart(chart, use_container_width=False)

        time.sleep(0.5)

    st.success("✅ Optimization Finished")




