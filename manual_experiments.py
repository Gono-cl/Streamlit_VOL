import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
from core.optimization.bayesian_optimization import StepBayesianOptimizer
import plotly.express as px
from skopt.space import Real, Categorical
from sklearn.preprocessing import LabelEncoder
from core.utils import db_handler
import os
import json
import dill as pickle  

SAVE_DIR = "resumable_manual_runs"
os.makedirs(SAVE_DIR, exist_ok=True)

# --- Resume Section ---
st.sidebar.markdown("---")
resume_file = st.sidebar.selectbox(
    "🔄 Resume from Previous Manual Campaign", 
    options=["None"] + os.listdir(SAVE_DIR)
)
if resume_file != "None" and st.sidebar.button("Load Previous Manual Campaign"):
    run_path = os.path.join(SAVE_DIR, resume_file)
    with open(os.path.join(run_path, "optimizer.pkl"), "rb") as f:
        st.session_state.manual_optimizer = pickle.load(f)
    df = pd.read_csv(os.path.join(run_path, "manual_data.csv"))
    with open(os.path.join(run_path, "metadata.json"), "r") as f:
        metadata = json.load(f)

    # Restore session state
    st.session_state.manual_data = df.to_dict("records")
    st.session_state.manual_variables = metadata["variables"]
    st.session_state.iteration = metadata.get("iteration", len(df))
    st.session_state.campaign_name = resume_file
    st.session_state.n_init = metadata.get("n_init", 1)
    st.session_state.total_iters = metadata.get("total_iters", 1)
    st.session_state.response = metadata.get("response", "Yield")
    st.session_state.manual_initialized = True
    st.session_state.initial_results_submitted = True

    # Use the restored response variable
    response = st.session_state.response

    # Re-observe all previous data points in the optimizer
    if st.session_state.manual_optimizer is not None:
        for row in st.session_state.manual_data:
            x = [row[name] for name, *_ in st.session_state.manual_variables]
            y_val = row[response]
            st.session_state.manual_optimizer.observe(x, -y_val)

    st.success(f"Loaded campaign: {resume_file}")

st.title("🧰 Manual Optimization Campaign")

# --- Reset Button ---
if st.button("🔄 Reset Campaign"):
    for key in [
        "manual_variables", "manual_data", "manual_optimizer",
        "manual_initialized", "suggestions", "iteration",
        "initial_results_submitted", "next_suggestion_cached",
        "submitted_initial", "edited_initial_df", "n_init", "total_iters", "edit_mode", "recalc_needed"
    ]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()

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

    # Keep only variables defined by the user
    input_vars = [name for name, *_ in st.session_state.manual_variables]
    cols_to_plot = input_vars + [response_name]
    df = df[cols_to_plot]

    st.markdown("### 🔀 Parallel Coordinates Plot")

    # Encode object columns numerically and prepare label legend
    legend_entries = []
    for col in df.columns:
        if df[col].dtype == object:
            le = LabelEncoder()
            df[col] = le.fit_transform(df[col])
            legend_entries.append((col, dict(enumerate(le.classes_))))

    labels = {col: col for col in df.columns}

    fig = px.parallel_coordinates(
        df,
        color=response_name,
        color_continuous_scale=px.colors.sequential.Viridis,
        labels=labels
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

    # Display legend for categorical variables
    if legend_entries:
        st.markdown("### 🏷️ Categorical Legends")
        for col, mapping in legend_entries:
            st.markdown(f"**{col}**:")
            for code, label in mapping.items():
                st.markdown(f"- `{code}` → `{label}`")

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
if "n_init" not in st.session_state:
    st.session_state.n_init = 1
if "total_iters" not in st.session_state:
    st.session_state.total_iters = 1
if "edit_mode" not in st.session_state:
    st.session_state.edit_mode = False
if "recalc_needed" not in st.session_state:
    st.session_state.recalc_needed = False

experiment_name = st.text_input("Experiment Name")
experiment_notes = st.text_area("Notes (optional)")
experiment_date = st.date_input("Experiment date")

# Use the experiment name provided by the user, or fall back to a default
run_name = experiment_name.strip() if experiment_name.strip() else "manual_experiment"
st.session_state["campaign_name"] = run_name  # Save it in session state
run_path = os.path.join(SAVE_DIR, run_name)
os.makedirs(run_path, exist_ok=True)

# Add a "Save Campaign" button in the sidebar
if st.sidebar.button("💾 Save Campaign"):
    # Save optimizer
    with open(os.path.join(run_path, "optimizer.pkl"), "wb") as f:
        pickle.dump(st.session_state.manual_optimizer, f)
    # Save data
    df = pd.DataFrame(st.session_state.manual_data)
    df.to_csv(os.path.join(run_path, "manual_data.csv"), index=False)
    # Save metadata
    metadata = {
        "variables": st.session_state.manual_variables,
        "iteration": st.session_state.get("iteration", len(df)),
        "n_init": st.session_state.n_init,
        "total_iters": st.session_state.total_iters,
        "response": st.session_state.response
    }
    with open(os.path.join(run_path, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=4)
    st.sidebar.success(f"Campaign '{run_name}' saved successfully!")

# --- Variable Definition ---
st.subheader("🔧 Define Variables")

if "var_type" not in st.session_state:
    st.session_state.var_type = "Continuous"

st.session_state.var_type = st.selectbox("Variable Type", ["Continuous", "Categorical"], key="var_type_select")

with st.form("manual_var_form"):
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        var_name = st.text_input("Variable Name")
    with col2:
        if st.session_state.var_type == "Continuous":
            lower = st.number_input("Lower Bound", value=0.0, format="%.4f")
            upper = st.number_input("Upper Bound", value=1.0, format="%.4f")
        else:
            categories = st.text_input("Categories (comma-separated)", value="Type")
    with col3:
        unit = st.text_input("Unit")

    add_var = st.form_submit_button("Add Variable")
    if add_var and var_name:
        if st.session_state.var_type == "Continuous" and lower < upper:
            st.session_state.manual_variables.append((var_name, lower, upper, unit, "continuous"))
        elif st.session_state.var_type == "Categorical" and categories:
            values = [x.strip() for x in categories.split(",") if x.strip() != ""]
            st.session_state.manual_variables.append((var_name, values, None, unit, "categorical"))

if st.session_state.manual_variables:
    st.markdown("### 📋 Current Variables")
    for i, (name, val1, val2, unit, vtype) in enumerate(st.session_state.manual_variables):
        if vtype == "continuous":
            st.write(f"{i+1}. **{name}**: {val1} to {val2} {unit} (continuous)")
        else:
            st.write(f"{i+1}. **{name}**: {val1} {unit} (categorical)")
else:
    st.info("Define at least one variable to start.")

# --- Experiment Setup ---
st.subheader("⚙️ Experiment Setup")
col5, col6 = st.columns(2)
with col5:
    response = st.selectbox("Response to Optimize", ["Yield", "Conversion", "Transformation", "Productivity"])
    st.session_state.response = response
with col6:
    n_init = st.number_input(
        "# Initial Experiments", min_value=1, max_value=50, value=st.session_state.n_init, key="n_init"
    )
    total_iters = st.number_input(
        "Total Iterations", min_value=1, max_value=100, value=st.session_state.total_iters, key="total_iters"
    )

# --- Suggest Initial Experiments ---
if st.button("🚀 Suggest Initial Experiments"):
    if not st.session_state.manual_variables:
        st.warning("Please define at least one variable first.")
    else:
        opt_vars = []
        for name, val1, val2, _, vtype in st.session_state.manual_variables:
            if vtype == "continuous":
                opt_vars.append(Real(val1, val2, name=name))
            else:
                opt_vars.append(Categorical(val1, name=name))

        optimizer = StepBayesianOptimizer(opt_vars)
        st.session_state.manual_optimizer = optimizer
        st.session_state.manual_data = []
        st.session_state.manual_initialized = True
        st.session_state.iteration = 0
        st.session_state.initial_results_submitted = False
        st.session_state.next_suggestion_cached = None
        st.session_state.suggestions = [optimizer.suggest() for _ in range(n_init)]
        st.success("Initial experiments suggested successfully!")
    st.session_state.manual_optimizer = optimizer
    st.success("Optimizer initialized with mixed variable types!")

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

# --- Edit Previous Results ---
if len(st.session_state.manual_data) > 0:
    st.markdown("### ✏️ Edit Previous Results")
    if st.button("Enable Edit Mode"):
        st.session_state.edit_mode = True

    if st.session_state.edit_mode:
        edited_df = st.data_editor(
            pd.DataFrame(st.session_state.manual_data),
            key="edit_results_editor"
        )
        if st.button("Save Edits"):
            st.session_state.manual_data = edited_df.to_dict("records")
            st.session_state.edit_mode = False
            st.session_state.recalc_needed = True
            st.success("Edits saved! The optimizer will be recalculated.")
            st.rerun()

# --- Truncate to a specific experiment ---
if len(st.session_state.manual_data) > 0:
    st.markdown("###  Return to a Previous Experiment")
    max_idx = len(st.session_state.manual_data)
    trunc_idx = st.number_input(
        "Keep experiments up to (inclusive):",
        min_value=1, max_value=max_idx, value=max_idx, step=1
    )
    if st.button("Return and Restart From Here"):
        st.session_state.manual_data = st.session_state.manual_data[:trunc_idx]
        st.session_state.recalc_needed = True
        st.success(f"Returned to experiment {trunc_idx}. The optimizer will be recalculated.")
        st.rerun()

# --- Recalculate optimizer after edits or truncation ---
if st.session_state.recalc_needed:
    if st.session_state.manual_variables and st.session_state.manual_data:
        opt_vars = []
        for name, val1, val2, _, vtype in st.session_state.manual_variables:
            if vtype == "continuous":
                opt_vars.append(Real(val1, val2, name=name))
            else:
                opt_vars.append(Categorical(val1, name=name))
        optimizer = StepBayesianOptimizer(opt_vars)
        for row in st.session_state.manual_data:
            x = [row[name] for name, *_ in st.session_state.manual_variables]
            y_val = row[st.session_state.response]
            optimizer.observe(x, -y_val)
        st.session_state.manual_optimizer = optimizer
        st.session_state.iteration = len(st.session_state.manual_data)
    st.session_state.recalc_needed = False

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

    # Save to experiment database
    if st.button("💾 Save to Database"):
        best_row = df_results.loc[df_results[response].idxmax()].to_dict()

        optimization_settings = {
            "initial_experiments": n_init,
            "total_iterations": total_iters,
            "objective": response,
            "method": "Manual Bayesian Optimization"
        }

        db_handler.save_experiment(
            user_email=st.user.email,
            name=experiment_name,
            notes=experiment_notes,
            variables=st.session_state.manual_variables,
            df_results=df_results,
            best_result=best_row,
            settings=optimization_settings
        )
        st.success("✅ Experiment saved successfully!")











