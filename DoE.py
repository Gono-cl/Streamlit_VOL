import os
from datetime import datetime
import itertools
import json

import numpy as np
import pandas as pd
import streamlit as st

from core.utils.export_tools import export_to_csv, export_to_excel


SAVE_DIR = "resumable_doe_runs"
os.makedirs(SAVE_DIR, exist_ok=True)


st.title("Design of Experiments (DoE)")

# Session state init
if "doe_factors" not in st.session_state:
    st.session_state.doe_factors = []
if "doe_design" not in st.session_state:
    st.session_state.doe_design = None


st.subheader("Define Factors")
with st.form("add_factor"):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        name = st.text_input("Name", placeholder="e.g., temperature")
    with c2:
        ftype = st.selectbox("Type", options=["continuous", "categorical"])
    with c3:
        unit = st.text_input("Unit", placeholder="e.g., °C")
    with c4:
        placeholder = "e.g., 3 (levels)" if ftype == "continuous" else "A, B, C"
        extra = st.text_input("Levels / Categories", placeholder=placeholder)

    # Additional bounds for continuous
    c5, c6 = st.columns(2)
    lower, upper = None, None
    if ftype == "continuous":
        with c5:
            lower = st.number_input("Lower bound", value=0.0, format="%.6f")
        with c6:
            upper = st.number_input("Upper bound", value=1.0, format="%.6f")

    submitted = st.form_submit_button("Add Factor")
    if submitted:
        if not name:
            st.warning("Please provide a factor name.")
        elif ftype == "continuous":
            try:
                levels = int(extra) if extra else 3
                if levels < 2:
                    raise ValueError
            except Exception:
                st.warning("Provide a valid integer number of levels (>=2).")
            else:
                if lower is None or upper is None or lower >= upper:
                    st.warning("Ensure lower < upper bounds for continuous factors.")
                else:
                    st.session_state.doe_factors.append({
                        "name": name,
                        "type": ftype,
                        "unit": unit,
                        "lower": float(lower),
                        "upper": float(upper),
                        "levels": int(levels)
                    })
        else:
            # categorical
            categories = [c.strip() for c in extra.split(",") if c.strip()]
            if len(categories) < 2:
                st.warning("Provide at least two categories, comma-separated.")
            else:
                st.session_state.doe_factors.append({
                    "name": name,
                    "type": ftype,
                    "unit": unit,
                    "categories": categories
                })


if st.session_state.doe_factors:
    st.markdown("### Current Factors")
    for i, f in enumerate(st.session_state.doe_factors, 1):
        if f["type"] == "continuous":
            st.write(f"{i}. {f['name']} [{f['unit'] or '-'}]: {f['lower']} to {f['upper']} (levels={f['levels']})")
        else:
            st.write(f"{i}. {f['name']} [{f['unit'] or '-'}]: {', '.join(f['categories'])}")
    cdel1, cdel2 = st.columns(2)
    with cdel1:
        if st.button("Remove Last Factor"):
            st.session_state.doe_factors.pop()
    with cdel2:
        if st.button("Clear All Factors"):
            st.session_state.doe_factors = []
else:
    st.info("No factors added yet.")


st.subheader("Design Settings")
c1, c2, c3 = st.columns(3)
with c1:
    design_type = st.selectbox("Design type", ["Full Factorial", "Latin Hypercube"], index=0)
with c2:
    replicates = st.number_input("Replicates", min_value=1, max_value=20, value=1, step=1)
with c3:
    randomize = st.checkbox("Randomize order", value=True)

seed = None
if randomize:
    seed = st.number_input("Random seed", min_value=0, max_value=2**31-1, value=42, step=1)

lhs_samples = None
if design_type == "Latin Hypercube":
    lhs_samples = st.number_input("Number of samples", min_value=1, value=10, step=1)


def generate_full_factorial(factors: list) -> pd.DataFrame:
    grids = []
    names = []
    for f in factors:
        names.append(f["name"])
        if f["type"] == "continuous":
            grid = np.linspace(f["lower"], f["upper"], int(f["levels"]))
            grids.append(grid.tolist())
        else:
            grids.append(list(f["categories"]))
    runs = list(itertools.product(*grids)) if grids else []
    df = pd.DataFrame(runs, columns=names)
    return df


def generate_lhs(factors: list, n_samples: int, rng: np.random.Generator) -> pd.DataFrame:
    cont = [f for f in factors if f["type"] == "continuous"]
    cat = [f for f in factors if f["type"] == "categorical"]
    if len(cont) == 0:
        st.warning("LHS requires at least one continuous factor. Falling back to uniform categorical sampling only.")
    # Latin Hypercube for continuous
    df_parts = []
    if cont:
        # Basic LHS implementation
        dims = len(cont)
        # For each dimension, create n_samples strata and shuffle
        lhs = np.zeros((n_samples, dims))
        for j in range(dims):
            perm = rng.permutation(n_samples)
            lhs[:, j] = (perm + rng.random(n_samples)) / n_samples
        # Scale to bounds
        for j, f in enumerate(cont):
            low, up = f["lower"], f["upper"]
            lhs[:, j] = low + lhs[:, j] * (up - low)
        df_cont = pd.DataFrame(lhs, columns=[f["name"] for f in cont])
        df_parts.append(df_cont)
    # Uniform random sampling for categoricals
    if cat:
        data = {}
        for f in cat:
            choices = rng.choice(f["categories"], size=n_samples, replace=True)
            data[f["name"]] = choices
        df_cat = pd.DataFrame(data)
        df_parts.append(df_cat)
    if not df_parts:
        return pd.DataFrame()
    df = pd.concat(df_parts, axis=1)
    # Reorder columns according to original factors order
    df = df[[f["name"] for f in factors]]
    return df


def replicate_and_randomize(df: pd.DataFrame, reps: int, do_shuffle: bool, rng: np.random.Generator) -> pd.DataFrame:
    if df.empty:
        return df
    df_rep = pd.concat([df.copy() for _ in range(reps)], ignore_index=True)
    df_rep["Run"] = np.arange(1, len(df_rep) + 1)
    if do_shuffle:
        df_rep = df_rep.sample(frac=1.0, random_state=rng.integers(0, 2**31-1)).reset_index(drop=True)
        df_rep["Run"] = np.arange(1, len(df_rep) + 1)
    return df_rep


if st.button("Generate Design"):
    rng = np.random.default_rng(int(seed) if seed is not None else None)
    if design_type == "Full Factorial":
        base_df = generate_full_factorial(st.session_state.doe_factors)
    else:
        base_df = generate_lhs(st.session_state.doe_factors, int(lhs_samples or 0), rng)
    final_df = replicate_and_randomize(base_df, int(replicates), bool(randomize), rng)
    st.session_state.doe_design = final_df


if st.session_state.doe_design is not None and not st.session_state.doe_design.empty:
    st.subheader("Design Preview")
    st.dataframe(st.session_state.doe_design)

    st.markdown("### Export")
    export_to_csv(st.session_state.doe_design, filename="doe_design.csv")
    export_to_excel(st.session_state.doe_design, filename="doe_design.xlsx")

    st.markdown("### Save Plan")
    exp_name = st.text_input("Plan name", value=datetime.now().strftime("DoE_%Y%m%d_%H%M%S"))
    if st.button("Save DoE Plan"):
        run_dir = os.path.join(SAVE_DIR, exp_name)
        os.makedirs(run_dir, exist_ok=True)
        # Save design
        st.session_state.doe_design.to_csv(os.path.join(run_dir, "design.csv"), index=False)
        # Save metadata
        meta = {
            "factors": st.session_state.doe_factors,
            "design_type": design_type,
            "replicates": int(replicates),
            "randomize": bool(randomize),
            "seed": int(seed) if seed is not None else None,
            "lhs_samples": int(lhs_samples) if lhs_samples is not None else None,
        }
        with open(os.path.join(run_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        st.success(f"Saved DoE plan to {run_dir}")
