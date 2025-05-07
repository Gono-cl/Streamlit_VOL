import streamlit as st
import pandas as pd
import os
import pickle
import json
import altair as alt
from datetime import datetime

SAVE_DIR = "resumable_runs"

st.title("🔍 Preview Optimization Run")

# --- Select a run to preview ---
runs = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
selected_run = st.selectbox("Select a Run to Preview", options=["None"] + runs)

if selected_run != "None":
    run_path = os.path.join(SAVE_DIR, selected_run)
    try:
        df = pd.read_csv(os.path.join(run_path, "experiment_data.csv"))
        with open(os.path.join(run_path, "metadata.json"), "r") as f:
            metadata = json.load(f)

        st.markdown(f"### 📄 Run: `{selected_run}`")
        st.markdown(f"**Variables:** {[v[0] for v in metadata['variables']]}")
        st.markdown(f"**Target:** `{metadata['response']}`")
        st.markdown(f"**Mode:** `{metadata['simulation_mode']}`")
        st.markdown(f"**Progress:** {len(df)} / {metadata['total_iterations']} experiments")

        st.dataframe(df)

        st.markdown("---")
        st.markdown("### 📈 Measurement vs Experiment")
        st.line_chart(df.set_index("Experiment #")["Measurement"])

        st.markdown("---")
        st.markdown("### 🔬 Variable Relationships")
        scatter_rows = [st.columns(2) for _ in range((len(metadata['variables']) + 1) // 2)]
        scatter_placeholders = [col.empty() for row in scatter_rows for col in row][:len(metadata['variables'])]

        for idx, (name, low, high, _) in enumerate(metadata['variables']):
            chart = alt.Chart(df).mark_circle(size=60).encode(
                x=alt.X(f"{name}:Q", scale=alt.Scale(domain=[low, high])),
                y="Measurement:Q"
            ).properties(
                height=350,
                title=alt.TitleParams(text=f"{name} vs Measurement", anchor="middle")
            )
            scatter_placeholders[idx].altair_chart(chart, use_container_width=True)

        st.markdown("---")
        if st.button("▶ Resume This Run"):
            st.session_state.optimizer = pickle.load(open(os.path.join(run_path, "optimizer.pkl"), "rb"))
            st.session_state.experiment_data = df.to_dict("records")
            st.session_state.iteration = len(df)
            st.session_state.variables = metadata["variables"]
            st.session_state.response_to_optimize = metadata["response"]
            st.session_state.total_iterations = metadata["total_iterations"]
            st.session_state.simulation_mode = metadata["simulation_mode"]
            st.session_state.opc_url = metadata["opc_url"]
            st.session_state.run_name = selected_run
            st.session_state.optimization_running = True
            st.switch_page("single_objective.py")

    except Exception as e:
        st.error(f"Failed to load run: {e}")
else:
    st.info("Select a run from the dropdown above to preview its data.")
