"""Shared variable-entry and categorical reference controls."""

import streamlit as st

from .catalog import VARIABLE_OPTIONS
from .variables import normalize_variable, reference_point


def render_variable_form():
    st.session_state.variables = [normalize_variable(v) for v in st.session_state.get("variables", [])]
    locked = bool(st.session_state.get("optimization_running", False))
    type_column, name_column = st.columns([1, 2])
    with type_column:
        kind = st.selectbox("Variable Type", ["Continuous", "Categorical"],
                            key="campaign_variable_type", disabled=locked)
    with name_column:
        display_name = st.selectbox("Variable Name", list(VARIABLE_OPTIONS) + ["Custom..."], disabled=locked)
    with st.form("variable_form"):
        if display_name == "Custom...":
            name = st.text_input("Custom Variable ID", help="Use lowercase snake_case, e.g. catalyst", disabled=locked)
        else:
            name = VARIABLE_OPTIONS[display_name]
        columns = st.columns([2, 1] if kind == "Categorical" else [1, 1, 1])
        if kind == "Continuous":
            with columns[0]:
                low = st.number_input("Lower Bound", value=0.0, format="%.4f", disabled=locked)
            with columns[1]:
                high = st.number_input("Upper Bound", value=1.0, format="%.4f", disabled=locked)
        else:
            with columns[0]:
                low = st.text_input("Categories (comma-separated)", placeholder="EC1, EC2, EC3", disabled=locked)
                high = None
        with columns[-1]:
            unit = st.text_input("Unit", disabled=locked)
        if st.form_submit_button("Add Variable", disabled=locked):
            try:
                variable = normalize_variable((name, low, high, unit, kind.lower()))
                if variable[0] in [v[0] for v in st.session_state.variables]:
                    raise ValueError(f"Variable '{variable[0]}' is already defined.")
                st.session_state.variables.append(variable)
                st.session_state.pop("initial_design_preview", None)
                st.session_state.pop("initial_design_preview_bounds", None)
            except (TypeError, ValueError) as exc:
                st.warning(str(exc))

    if not st.session_state.variables:
        st.info("No variables added yet.")
        return
    st.markdown("### Added Variables")
    for index, (name, low, high, unit, variable_type) in enumerate(st.session_state.variables, 1):
        domain = ", ".join(low) if variable_type == "categorical" else f"from {low} to {high} {unit}"
        st.write(f"{index}. **{name}** ({variable_type}): {domain}")


def render_reference_categories(prefix):
    """Offer an explicit reference level; retain it across reruns and resume."""
    key = f"{prefix}_reference_categories"
    selected = dict(st.session_state.get(key, {}))
    variables = st.session_state.get("variables", [])
    categorical = [normalize_variable(v) for v in variables if normalize_variable(v)[4] == "categorical"]
    if categorical:
        st.caption("Sentinel reference: continuous variables use their midpoint; choose fixed categorical values below.")
    for name, levels, _, _, _ in categorical:
        widget_key = f"{prefix}_reference_{name}"
        if st.session_state.get(widget_key) not in levels:
            st.session_state[widget_key] = selected.get(name) if selected.get(name) in levels else levels[0]
        selected[name] = st.selectbox(f"Sentinel {name}", levels, key=widget_key,
                                      disabled=bool(st.session_state.get("optimization_running", False)))
    st.session_state[key] = {v[0]: selected[v[0]] for v in categorical}
    return reference_point(variables, selected)


def restore_reference_categories(prefix, categories):
    st.session_state[f"{prefix}_reference_categories"] = dict(categories)
    for name, value in categories.items():
        st.session_state[f"{prefix}_reference_{name}"] = value
