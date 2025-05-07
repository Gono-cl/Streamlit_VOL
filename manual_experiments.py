import streamlit as st
import pandas as pd
from datetime import datetime
import time
from core.optimization.bayesian_optimization import StepBayesianOptimizer

st.title("📝 Manual Optimization Campaign")

# --- Session State Initialization ---
for key in ["manual_variables", "manual_data", "manual_optimizer"]:
    if key not in st.session_state:
        st.session_state[key] = [] if key != "manual_optimizer" else None

# --- Campaign Metadata ---
st.subheader("📋 Campaign Metadata")
campaign_name = st.text_input("Campaign Name", value=f"manual_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
campaign_notes = st.text_area("Notes")

# --- Define Variables ---
st.subheader("⚙️ Define Variables")
with st.form("manual_variable_form"):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        var_name = st.text_input("Variable Name")
    with col2:
        lower = st.number_input("Lower Bound", format="%.4f")
    with col3:
        upper = st.number_input("Upper Bound", format="%.4f")
    with col4:
        unit = st.text_input("Unit")
    submitted = st.form_submit_button("Add Variable")
    if submitted and var_name and lower < upper:
        st.session_state.manual_variables.append((var_name, lower, upper, unit))

if st.session_state.manual_variables:
    st.markdown("### 📌 Defined Variables")
    for i, (n, l, u, un) in enumerate(st.session_state.manual_variables):
        st.write(f"{i+1}. **{n}**: {l} to {u} {un}")

# --- Objective Function ---
st.subheader("🎯 Objective")
objective_name = st.selectbox("Select Objective", ["Yield", "Conversion", "Transformation", "Productivity"])

# --- Upload Existing Data ---
st.subheader("📤 Upload Initial Data")
uploaded_file = st.file_uploader("Upload CSV with previous experiments", type="csv")
if uploaded_file:
    df_uploaded = pd.read_csv(uploaded_file)
    st.session_state.manual_data = df_uploaded.to_dict("records")
    st.success("✅ Data uploaded!")
    st.dataframe(df_uploaded)

# --- Manually Add Data ---
st.subheader("➕ Add Manual Entry")
manual_input = {}
cols = st.columns(len(st.session_state.manual_variables) + 1)
for i, (name, _, _, _) in enumerate(st.session_state.manual_variables):
    manual_input[name] = cols[i].number_input(name, format="%.4f")
manual_input["Measurement"] = cols[-1].number_input("Measurement", format="%.4f")
if st.button("Add Entry"):
    st.session_state.manual_data.append(manual_input)
    st.success("✅ Entry added!")

# --- Show Current Dataset ---
if st.session_state.manual_data:
    st.markdown("### 📊 Current Data")
    df_current = pd.DataFrame(st.session_state.manual_data)
    st.dataframe(df_current)

# --- Optimizer & Suggestion ---
if len(st.session_state.manual_data) >= 2:
    st.subheader("🤖 Optimization Suggestion")
    if st.session_state.manual_optimizer is None:
        st.session_state.manual_optimizer = StepBayesianOptimizer([
            (n, l, u) for n, l, u, _ in st.session_state.manual_variables
        ])
        for row in st.session_state.manual_data:
            x = [row[n] for n, *_ in st.session_state.manual_variables]
            y = -row["Measurement"]
            st.session_state.manual_optimizer.observe(x, y)

    x_next = st.session_state.manual_optimizer.suggest()
    suggestion = {name: val for (name, *_), val in zip(st.session_state.manual_variables, x_next)}
    st.write("🔬 **Suggested Experiment**:", suggestion)

    # Add result from suggestion
    result_value = st.number_input("Enter result for suggested experiment (Measurement)", format="%.4f")
    if st.button("Confirm and Add Result"):
        new_row = {**suggestion, "Measurement": result_value}
        st.session_state.manual_data.append(new_row)
        st.success("✅ Result added and optimizer updated!")
        st.rerun()
