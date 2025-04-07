# experiment_database.py
import streamlit as st
from core.utils import db_handler

st.title("📚 Experiment History")

experiments = db_handler.list_experiments()

if not experiments:
    st.info("No experiments saved yet.")
else:
    selected_id = st.selectbox("Select an experiment", experiments, format_func=lambda x: f"{x[1]} ({x[2]})")

    if selected_id:
        exp_data = db_handler.load_experiment(selected_id[0])

        st.subheader("📋 Metadata")
        st.write(f"**Name:** {exp_data['name']}")
        st.write(f"**Timestamp:** {exp_data['timestamp']}")
        st.write(f"**Notes:** {exp_data['notes']}")

        st.subheader("⚙️ Optimization Settings")
        settings = exp_data["settings"]

        if settings:
            st.write(f"**Initial Experiments:** {settings['initial_experiments']}")
            st.write(f"**Total Iterations:** {settings['total_iterations']}")
            if "objective" in settings:
                st.write(f"**Objective:** {settings['objective']}")
            elif "objectives" in settings:
                st.write("**Objectives:** " + ", ".join(settings["objectives"]))
            else:
                st.write("No objective info found.")

            st.write(f"**Method:** {settings['method']}")


        st.subheader("📈 Results")
        st.dataframe(exp_data["df_results"])

        st.subheader("🥇 Best Result")
        st.write(exp_data["best_result"])
