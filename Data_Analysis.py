import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt
import plotly.express as px
import plotly.graph_objects as go
import seaborn as sns
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from sklearn.ensemble import RandomForestRegressor

from core.utils.export_tools import export_to_csv, export_to_excel


st.title("Data Analysis")


def list_runs(base_dir: str) -> List[str]:
    if not os.path.exists(base_dir):
        return []
    return [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]


def load_run_df(base_dir: str, run_name: str) -> Optional[pd.DataFrame]:
    data_path = os.path.join(base_dir, run_name, "experiment_data.csv")
    if os.path.exists(data_path):
        try:
            return pd.read_csv(data_path)
        except Exception:
            return None
    return None


def intersect_columns(dfs: List[pd.DataFrame]) -> List[str]:
    common = None
    for df in dfs:
        cols = set(df.columns)
        common = cols if common is None else (common & cols)
    return sorted(list(common)) if common else []


def numeric_columns(df: pd.DataFrame, exclude: Optional[List[str]] = None) -> List[str]:
    exclude = set(exclude or [])
    return [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]


def response_surface(df: pd.DataFrame, x: str, y: str, response: str, grid: int = 30) -> pd.DataFrame:
    # Fit a simple RF model and predict on grid
    features = [col for col in df.columns if col not in [response]]
    # Keep only numeric features
    features = [c for c in features if pd.api.types.is_numeric_dtype(df[c])]
    if x not in features:
        features.append(x)
    if y not in features:
        features.append(y)
    # Build model
    model = RandomForestRegressor(n_estimators=200, random_state=42)
    X = df[features].copy()
    y_vals = pd.to_numeric(df[response], errors="coerce")
    mask = X.notnull().all(axis=1) & y_vals.notnull()
    X = X[mask]
    y_vals = y_vals[mask]
    if len(X) < 5:
        return pd.DataFrame()
    model.fit(X, y_vals)

    x_min, x_max = float(df[x].min()), float(df[x].max())
    y_min, y_max = float(df[y].min()), float(df[y].max())
    gx = np.linspace(x_min, x_max, grid)
    gy = np.linspace(y_min, y_max, grid)
    grid_pts = []
    base = X.median(numeric_only=True)
    for xv in gx:
        for yv in gy:
            row = base.copy()
            row[x] = xv
            row[y] = yv
            grid_pts.append(row.values)
    Xg = pd.DataFrame(grid_pts, columns=base.index)
    preds = model.predict(Xg)
    out = pd.DataFrame({
        x: np.repeat(gx, len(gy)),
        y: np.tile(gy, len(gx)),
        response: preds
    })
    return out


def variable_importance(df: pd.DataFrame, response: str) -> Optional[pd.DataFrame]:
    features = [c for c in df.columns if c != response and pd.api.types.is_numeric_dtype(df[c])]
    if not features:
        return None
    y = pd.to_numeric(df[response], errors="coerce")
    X = df[features]
    mask = X.notnull().all(axis=1) & y.notnull()
    X = X[mask]
    y = y[mask]
    if len(X) < 5:
        return None
    model = RandomForestRegressor(n_estimators=300, random_state=42)
    model.fit(X, y)
    imp = pd.DataFrame({"Variable": features, "Importance": model.feature_importances_}).sort_values("Importance", ascending=False)
    return imp


with st.sidebar:
    st.header("Data Source")
    dataset_type = st.selectbox("Dataset", ["Single Objective", "Multi Objective", "Manual"])
    if dataset_type == "Single Objective":
        base_dir = "resumable_runs"
    elif dataset_type == "Multi Objective":
        base_dir = "resumable_multiobjective_runs"
    else:
        base_dir = "resumable_manual_runs"

    runs = list_runs(base_dir)
    selected_runs = st.multiselect("Select runs", runs)
    load_btn = st.button("Load Data")


if load_btn:
    dfs = []
    for r in selected_runs:
        df = load_run_df(base_dir, r)
        if df is not None and not df.empty:
            df["__run__"] = r
            dfs.append(df)
    if not dfs:
        st.warning("No data loaded. Check selected runs.")
    else:
        # Align on common columns
        common_cols = intersect_columns(dfs)
        # keep run marker
        if "__run__" not in common_cols:
            common_cols.append("__run__")
        data = pd.concat([d[common_cols].copy() for d in dfs], ignore_index=True)
        st.session_state.analysis_df = data


df = st.session_state.get("analysis_df")
if df is None:
    st.info("Select and load runs from the sidebar to begin analysis.")
    st.stop()


st.subheader("Dataset Preview")
st.dataframe(df.head(100))

# Determine candidate responses and variables
exclude_cols = ["Experiment #", "Timestamp", "Source", "__run__"]
num_cols = numeric_columns(df, exclude=exclude_cols)
if not num_cols:
    st.warning("No numeric columns found in the loaded data.")
    st.stop()

with st.expander("Column selection", expanded=True):
    c1, c2 = st.columns([2, 1])
    with c2:
        response = st.selectbox("Response", num_cols)
    with c1:
        feature_cols = st.multiselect("Variables (X)", [c for c in num_cols if c != response], default=[c for c in num_cols if c != response])

    # Filter dataframe to selected columns
    work_df = df[feature_cols + [response] + ["__run__"]].dropna()


st.subheader("Pairwise Analysis")
pair_cols = feature_cols + [response]
if len(pair_cols) >= 2:
    c1, c2 = st.columns(2)
    with c1:
        max_rows = st.number_input("Max rows", min_value=100, max_value=20000, value=3000, step=100)
    with c2:
        renderer = st.radio("Renderer", ["Plotly", "Seaborn (reg + hist + corr)"] , index=1)

    plot_df = work_df[pair_cols].copy().dropna()
    if len(plot_df) > max_rows:
        plot_df = plot_df.sample(int(max_rows), random_state=42)

    if renderer.startswith("Seaborn"):
        try:
            sns.set_theme(style="whitegrid")

            # Build PairGrid: lower = regplot, diag = hist, upper = corr text
            g = sns.PairGrid(plot_df, diag_sharey=False)
            g.map_lower(sns.regplot, scatter_kws={"s": 24, "alpha": 0.7, "edgecolor": "none"}, line_kws={"color": "black"})
            g.map_diag(sns.histplot, bins=15, color="gray", edgecolor="white")

            def corrfunc(x, y, **kws):
                try:
                    r = np.corrcoef(x, y)[0, 1]
                except Exception:
                    r = np.nan
                ax = plt.gca()
                ax.annotate(f"{r:.3f}" if r == r else "", xy=(0.5, 0.5), xycoords=ax.transAxes,
                            ha="center", va="center", fontsize=12)
                ax.set_axis_off()

            g.map_upper(corrfunc)
            st.pyplot(g.fig)
            plt.close(g.fig)
        except Exception:
            st.warning("Seaborn PairGrid failed; showing Plotly matrix instead.")
            fig = px.scatter_matrix(plot_df, dimensions=pair_cols)
            st.plotly_chart(fig, use_container_width=True)
    else:
        fig = px.scatter_matrix(plot_df, dimensions=pair_cols)
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Select at least two columns for pairwise plot.")


st.subheader("Response Surface")
rs_c1, rs_c2, rs_c3 = st.columns([1, 1, 1])
with rs_c1:
    x_var = st.selectbox("X variable", feature_cols)
with rs_c2:
    y_var = st.selectbox("Y variable", [c for c in feature_cols if c != x_var])
with rs_c3:
    grid_n = st.number_input("Grid size", min_value=15, max_value=100, value=30, step=5)

if x_var and y_var and response:
    surf_df = response_surface(work_df, x_var, y_var, response, grid=int(grid_n))
    if surf_df.empty:
        st.info("Not enough data to build a response surface.")
    else:
        # Build a contour heatmap using graph_objects for broader compatibility
        try:
            Z = surf_df.pivot(index=y_var, columns=x_var, values=response).sort_index(axis=0).sort_index(axis=1)
            x_vals = Z.columns.values.astype(float)
            y_vals = Z.index.values.astype(float)
            z_vals = Z.values
            fig2 = go.Figure(data=go.Contour(
                x=x_vals,
                y=y_vals,
                z=z_vals,
                colorscale='Turbo',
                contours=dict(coloring='heatmap', showlabels=True)
            ))
            fig2.update_layout(xaxis_title=x_var, yaxis_title=y_var)
            st.plotly_chart(fig2, use_container_width=True)
        except Exception:
            st.warning("Could not render contour plot; falling back to scatter.")
            st.plotly_chart(px.scatter(surf_df, x=x_var, y=y_var, color=response), use_container_width=True)


st.subheader("Variable Importance")
imp_df = variable_importance(work_df, response)
if imp_df is None or imp_df.empty:
    st.info("Not enough data for importance analysis.")
else:
    chart = alt.Chart(imp_df).mark_bar().encode(
        x=alt.X("Importance:Q", title="Importance"),
        y=alt.Y("Variable:N", sort='-x', title="Variable"),
        color=alt.Color("Importance:Q", scale=alt.Scale(scheme='turbo'))
    ).properties(height=300)
    st.altair_chart(chart, use_container_width=True)


st.subheader("Correlation Heatmap")
corr_cols = feature_cols + [response]
if len(corr_cols) >= 2:
    corr = work_df[corr_cols].corr(numeric_only=True)
    corr_long = corr.reset_index().melt(id_vars='index')
    corr_long.columns = ["Var1", "Var2", "Correlation"]
    heat = alt.Chart(corr_long).mark_rect().encode(
        x=alt.X("Var1:N", title=None),
        y=alt.Y("Var2:N", title=None),
        color=alt.Color("Correlation:Q", scale=alt.Scale(scheme='spectral', domain=[-1, 1]))
    ).properties(height=300)
    st.altair_chart(heat, use_container_width=True)


st.subheader("Export")
export_to_csv(work_df, filename="analysis_dataset.csv")
export_to_excel(work_df, filename="analysis_dataset.xlsx")
