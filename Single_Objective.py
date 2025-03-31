import streamlit as st
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.figure import Figure 
import numpy as np
import pandas as pd
import altair as alt
import time
from core.optimization.bayesian_optimization import BayesianOptimizer
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils import db_handler 

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

    # Before the loop — set up the layout and placeholders once
    results_chart_container = st.empty()

    num_vars = len(st.session_state.variables)
    scatter_rows = [st.columns(2) for _ in range((num_vars + 1) // 2)]
    scatter_placeholders = [
        col.empty() for row in scatter_rows for col in row
    ][:num_vars]  # Flatten and match number of variables

    # Initialize data storage
    experiment_data = []

    # Optimization loop
    for i in range(iterations):
        x = [np.random.uniform(low, high) for _, low, high, _ in st.session_state.variables]
        y = -np.sum(x)  # Replace with real objective function

        experiment_data.append({
            "Experiment #": i + 1,
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **{name: val for (name, *_), val in zip(st.session_state.variables, x)},
            "Measurement": -y
        })

        # Create DataFrame from collected data
        df_results = pd.DataFrame(experiment_data)

        # Update results line chart
        results_chart_container.line_chart(df_results[["Experiment #", "Measurement"]].set_index("Experiment #"))

        # Update scatter charts
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
            
            # Save experiment in database
            db_handler.save_experiment(
                name=experiment_name,
                notes=experiment_notes,
                variables=st.session_state.variables,
                df_results=df_results,
            )

        time.sleep(0.5)

    st.success("✅ Optimization Finished")
    
    # Find best result (maximizing measurement)
    best_row = df_results.loc[df_results["Measurement"].idxmax()]
    st.markdown("### 🥇 Best Result")
    st.write(best_row)


    # Export buttons
    Timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    export_to_csv(df_results, f"{Timestamp}_optimization_results.csv")
    export_to_excel(df_results, f"{Timestamp}_optimization_results.csv")






    



