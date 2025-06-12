import streamlit as st
import pandas as pd
import altair as alt
from core.utils import db_handler
from core.utils.generate_report import generate_report
import plotly.express as px
from sklearn.preprocessing import LabelEncoder

st.title("📚 Experiment Database")
st.markdown("### Experiment History")

experiments = db_handler.list_experiments()

if not experiments:
    st.info("No experiments saved yet.")
else:
    # Add a title and make the table expandable
    with st.expander("🗂️ Show/Hide Experiment List & Delete", expanded=False):
        st.markdown("### Select experiments to delete")
        exp_df = pd.DataFrame(experiments, columns=["ID", "Name", "Timestamp"])
        exp_df["Delete?"] = False  # Add a checkbox column

        selected = st.data_editor(
            exp_df,
            column_config={"Delete?": st.column_config.CheckboxColumn("Delete?")},
            num_rows="dynamic",
            use_container_width=True,
            key="exp_editor"
        )

        # Get IDs of selected experiments
        to_delete = selected[selected["Delete?"] == True]["ID"].tolist()
        delete_btn = st.button("🗑️ Delete Selected Experiments", disabled=not to_delete)
        if delete_btn and to_delete:
            db_handler.delete_experiments(to_delete)
            st.success(f"Deleted {len(to_delete)} experiment(s).")
            st.rerun()

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
        df_results = exp_data["df_results"].copy()

        # Ensure compatibility with Arrow by converting object columns to string
        for col in df_results.columns:
            if df_results[col].dtype == 'object':
                df_results[col] = df_results[col].astype(str)

        st.dataframe(df_results, use_container_width=True)

        st.subheader("🥇 Best Result")
        st.write(exp_data["best_result"])

        # --- Show Pareto front if available ---
        if isinstance(exp_data["best_result"], list) and len(exp_data["best_result"]) > 0 and isinstance(exp_data["best_result"][0], dict):
            st.markdown("### 🏆 Pareto Front")
            pareto_df = pd.DataFrame(exp_data["best_result"])
            st.dataframe(pareto_df, use_container_width=True)

            # --- Full Pareto plot: all experiments + Pareto front ---
            if "objectives" in settings and len(settings["objectives"]) == 2:
                obj_x, obj_y = settings["objectives"][:2]

                # Calculate dynamic axis ranges with a small buffer
                x_vals = df_results[obj_x]
                y_vals = df_results[obj_y]
                x_range = x_vals.max() - x_vals.min()
                y_range = y_vals.max() - y_vals.min()
                x_buffer = x_range * 0.05 if x_range > 0 else 1
                y_buffer = y_range * 0.05 if y_range > 0 else 1
                x_min = x_vals.min() - x_buffer
                x_max = x_vals.max() + x_buffer
                y_min = y_vals.min() - y_buffer
                y_max = y_vals.max() + y_buffer

                # Plot all experiments as blue circles
                base_chart = alt.Chart(df_results).mark_circle(size=60, color="blue").encode(
                    x=alt.X(obj_x, title=obj_x, scale=alt.Scale(domain=[x_min, x_max])),
                    y=alt.Y(obj_y, title=obj_y, scale=alt.Scale(domain=[y_min, y_max])),
                    tooltip=list(df_results.columns)
                )

                # Overlay Pareto front as a red line with points
                if not pareto_df.empty:
                    pareto_line = alt.Chart(pareto_df).mark_line(point=True, color="red").encode(
                        x=alt.X(obj_x, title=obj_x, scale=alt.Scale(domain=[x_min, x_max])),
                        y=alt.Y(obj_y, title=obj_y, scale=alt.Scale(domain=[y_min, y_max])),
                        tooltip=list(pareto_df.columns)
                    )
                    st.altair_chart(base_chart + pareto_line, use_container_width=True)
                else:
                    st.altair_chart(base_chart, use_container_width=True)

        # Generate and show charts
        if "Response" in df_results.columns:
            response_name = "Response"
        elif "Measurement" in df_results.columns:
            response_name = "Measurement"
        else:
            # For multi-objective: let user pick one
            objective_cols = [col for col in df_results.columns if col not in ["Experiment #", "Timestamp"]]
            if objective_cols:
                response_name = st.selectbox("Select objective to view:", objective_cols)
            else:
                st.error("No objective columns found in results.")
                st.stop()

        df_results[response_name] = pd.to_numeric(df_results[response_name], errors="coerce")
        df_results["Iteration"] = range(1, len(df_results) + 1)

        progress_chart = alt.Chart(df_results).mark_line(point=True).encode(
            x=alt.X("Iteration", title="Experiment Number"),
            y=alt.Y(response_name, title=response_name),
            tooltip=["Iteration", response_name]
        ).properties(width=600, height=300)

        st.altair_chart(progress_chart, use_container_width=True)

        # Generate real parallel coordinates plot
        st.subheader("🔀 Parallel Coordinates Plot")

        df_encoded = df_results.copy()
        legend_entries = []

        for col in df_encoded.columns:
            if df_encoded[col].dtype == object or df_encoded[col].dtype.name == 'category':
                le = LabelEncoder()
                df_encoded[col] = le.fit_transform(df_encoded[col].astype(str))
                legend_entries.append((col, dict(enumerate(le.classes_))))

        input_vars = [c for c in df_encoded.columns if c not in ["Timestamp", "Iteration"]]

        parallel_chart = None
        if response_name in input_vars:
            parallel_chart = px.parallel_coordinates(
                df_encoded[input_vars],
                color=response_name,
                color_continuous_scale=px.colors.sequential.Viridis,
                labels={col: col for col in input_vars}
            )

            parallel_chart.update_layout(
                font=dict(size=20, color='black'),
                margin=dict(l=60, r=60, t=60, b=60),
                height=500,
                coloraxis_colorbar=dict(
                    title=response_name,
                    tickfont=dict(size=20, color='black'),
                    len=0.8,
                    thickness=20
                )
            )
            st.plotly_chart(parallel_chart, use_container_width=True)

            if legend_entries:
                st.markdown("### 🏷️ Categorical Legends")
                for col, mapping in legend_entries:
                    st.markdown(f"**{col}**:")
                    for code, label in mapping.items():
                        st.markdown(f"- `{code}` → `{label}`")


