import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from core.optimization.bayesian_optimization import StepBayesianOptimizer
import plotly.express as px


st.title("🧰 Manual Optimization Campaign")

st.markdown("""
This module allows users to manually define and run optimization campaigns step-by-step. 
At each step, the system will suggest a new experiment based on previous results.
""")

# --- Chart Function ---
def show_progress_chart(data: list, response_name: str):
    if len(data) == 0:
        return

    df_results = pd.DataFrame(data)
    df_results["Iteration"] = range(1, len(df_results) + 1)
    df_results[response_name] = pd.to_numeric(df_results[response_name], errors="coerce")

    st.markdown("### 📈 Optimization Progress")

    chart = alt.Chart(df_results).mark_line(point=True).encode(
        x=alt.X("Iteration", title="Experiment Number"),
        y=alt.Y(response_name, title=response_name),
        tooltip=["Iteration", response_name]
    ).properties(width=700, height=400)

    st.altair_chart(chart, use_container_width=True)

def show_parallel_coordinates(data: list, response_name: str):
    if len(data) == 0:
        return

    df = pd.DataFrame(data).copy()
    df[response_name] = pd.to_numeric(df[response_name], errors="coerce")

    # Keep only numeric columns + response
    input_vars = [name for name, *_ in st.session_state.manual_variables]
    cols_to_plot = input_vars + [response_name]
    df = df[cols_to_plot]

    st.markdown("### 🔀 Parallel Coordinates Plot")

    fig = px.parallel_coordinates(
        df,
        color=response_name,
        color_continuous_scale=px.colors.sequential.Viridis,
        labels={col: col for col in df.columns},
    )
    fig.update_layout(
        font=dict(size=20, color='black'),
        height=500,
        margin=dict(l=50, r=50, t=50, b=40),
        coloraxis_colorbar=dict(
            title=dict(
                text=response_name,
                font=dict(size=20, color='black'),    
            ),
            tickfont=dict(size=20, color='black'),
            len=0.8,
            thickness=40,
            tickprefix=" ",
            xpad=5
        )
    )

    st.plotly_chart(fig, use_container_width=True)


# --- Session State Initialization ---
if "manual_variables" not in st.session_state:
    st.session_state.manual_variables = []
if "manual_data" not in st.session_state:
    st.session_state.manual_data = []
if "manual_optimizer" not in st.session_state:
    st.session_state.manual_optimizer = None
if "manual_initialized" not in st.session_state:
    st.session_state.manual_initialized = False
if "suggestions" not in st.session_state:
    st.session_state.suggestions = []
if "iteration" not in st.session_state:
    st.session_state.iteration = 0
if "initial_results_submitted" not in st.session_state:
    st.session_state.initial_results_submitted = False
if "next_suggestion_cached" not in st.session_state:
    st.session_state.next_suggestion_cached = None
if "submitted_initial" not in st.session_state:
    st.session_state.submitted_initial = False
if "edited_initial_df" not in st.session_state:
    st.session_state.edited_initial_df = None

# --- Variable Definition ---
st.subheader("🔧 Define Variables")
with st.form("manual_var_form"):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        var_name = st.text_input("Variable Name")
    with col2:
        lower = st.number_input("Lower Bound", value=0.0, format="%.4f")
    with col3:
        upper = st.number_input("Upper Bound", value=1.0, format="%.4f")
    with col4:
        unit = st.text_input("Unit")
    add_var = st.form_submit_button("Add Variable")
    if add_var and var_name and lower < upper:
        st.session_state.manual_variables.append((var_name, lower, upper, unit))

if st.session_state.manual_variables:
    st.markdown("### 📋 Current Variables")
    for i, (name, low, high, unit) in enumerate(st.session_state.manual_variables):
        st.write(f"{i+1}. **{name}**: {low} to {high} {unit}")
else:
    st.info("Define at least one variable to start.")

# --- Experiment Setup ---
st.subheader("⚙️ Experiment Setup")
col5, col6 = st.columns(2)
with col5:
    response = st.selectbox("Response to Optimize", ["Yield", "Conversion", "Transformation", "Productivity"])
with col6:
    n_init = st.number_input("# Initial Experiments", min_value=1, max_value=50, value=3)
    total_iters = st.number_input("Total Iterations", min_value=1, max_value=100, value=10)

# --- Suggest Initial Experiments ---
if st.button("🚀 Suggest Initial Experiments"):
    if not st.session_state.manual_variables:
        st.warning("Please define at least two variables first.")
    else:
        opt_vars = [(v[0], v[1], v[2]) for v in st.session_state.manual_variables]
        optimizer = StepBayesianOptimizer(opt_vars)
        st.session_state.manual_optimizer = optimizer
        st.session_state.manual_data = []
        st.session_state.manual_initialized = True
        st.session_state.iteration = 0
        st.session_state.initial_results_submitted = False
        st.session_state.next_suggestion_cached = None
        st.session_state.suggestions = [optimizer.suggest() for _ in range(n_init)]

# --- Initial Results Input (only shown once) ---
if st.session_state.manual_initialized and st.session_state.suggestions and not st.session_state.initial_results_submitted:
    st.markdown("### 🧪 Initial Experiments (User Input Required)")
    st.caption("💡 Press Enter or click outside each cell to confirm your entry before submitting.")

    default_data = []
    for vals in st.session_state.suggestions:
        row = {name: val for (name, *_), val in zip(st.session_state.manual_variables, vals)}
        row[f"{response}"] = None
        default_data.append(row)

    edited_df = st.data_editor(
        pd.DataFrame(default_data),
        num_rows="fixed",
        key="initial_results_editor"
    )

    if st.button("✅ Submit Initial Results"):
        st.session_state.edited_initial_df = edited_df.copy()
        st.session_state.submitted_initial = True

# --- Process initial results AFTER submission ---
if st.session_state.submitted_initial:
    valid_rows = 0
    for _, row in st.session_state.edited_initial_df.iterrows():
        value = row.get(f"{response}")
        if value is None or str(value).strip() == "":
            continue

        try:
            y_val = float(value)
            x = [row[name] for name, *_ in st.session_state.manual_variables]
            st.session_state.manual_optimizer.observe(x, -y_val)
            row_data = row.to_dict()
            row_data[response] = y_val
            row_data["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.manual_data.append(row_data)
            valid_rows += 1
        except ValueError:
            st.error(f"Invalid number format: {value}")
            st.stop()

    if valid_rows < len(st.session_state.edited_initial_df):
        st.warning(f"Only {valid_rows} of {len(st.session_state.edited_initial_df)} experiments had valid results. Please complete all entries.")
        st.stop()

    st.session_state.iteration += valid_rows
    st.session_state.suggestions = []
    st.session_state.initial_results_submitted = True
    st.session_state.submitted_initial = False  # reset flag

# --- Always show the progress chart if data exists ---
if len(st.session_state.manual_data) > 0:
    show_progress_chart(st.session_state.manual_data, response)
    show_parallel_coordinates(st.session_state.manual_data, response)

# --- Button to get next suggestion manually ---
if (
    st.session_state.manual_initialized
    and st.session_state.manual_optimizer is not None
    and st.session_state.iteration < total_iters
    and st.session_state.initial_results_submitted
):
    if st.button("📎 Get Next Suggestion"):
        st.session_state.next_suggestion_cached = st.session_state.manual_optimizer.suggest()

# --- Show cached suggestion if available ---
if st.session_state.next_suggestion_cached is not None:
    st.markdown("### ▶️ Next Experiment Suggestion")
    next_row = {name: val for (name, *_), val in zip(st.session_state.manual_variables, st.session_state.next_suggestion_cached)}
    next_df = pd.DataFrame([next_row])
    st.dataframe(next_df, use_container_width=True)

    result = st.number_input(
        f"Result for {response} (Experiment {st.session_state.iteration + 1})",
        key=f"next_result_{st.session_state.iteration}"
    )

    if st.button("➕ Submit Result"):
        st.success("Result submitted successfully. To get a new suggestion, press '**📎 Get Next Suggestion**'. The graph is updated automatically.")
        if pd.notnull(result):
            x = [next_row[name] for name, *_ in st.session_state.manual_variables]
            y_val = float(result)
            st.session_state.manual_optimizer.observe(x, -y_val)

            row_data = {**next_row}
            row_data[response] = y_val
            row_data["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.manual_data.append(row_data)

            st.session_state.iteration += 1
            st.session_state.next_suggestion_cached = None  # clear after use

# --- All Iterations Completed ---
if st.session_state.iteration >= total_iters:
    st.markdown("### ✅ Optimization Completed")
    st.success("All iterations are completed! You can export the data or review the results.")

    df_results = pd.DataFrame(st.session_state.manual_data)
    st.dataframe(df_results, use_container_width=True)

    csv = df_results.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download Results as CSV", data=csv, file_name="manual_optimization_results.csv", mime="text/csv")











