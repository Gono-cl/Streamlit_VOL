import streamlit as st


def section_header(title: str, accent: str = "#ef4444", background: str = "#fef2f2") -> None:
    st.markdown(
        f"""
        <div style="margin: 0.9rem 0 0.85rem 0;">
            <div style="
                height: 2px;
                border-radius: 999px;
                background: linear-gradient(90deg, {accent}66, transparent);
                margin-bottom: 0.45rem;
            "></div>
            <div style="
                padding: 0.62rem 0.85rem;
                border-radius: 12px;
                border: 1px solid {accent}44;
                border-left: 7px solid {accent};
                background: linear-gradient(100deg, {background}, #ffffff);
                box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
            ">
                <div style="font-weight: 700; color: #0f172a; letter-spacing: 0.2px;">{title}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def ensure_defaults() -> None:
    # Single-objective reproducibility defaults
    if "single_repro_enabled" not in st.session_state:
        st.session_state.single_repro_enabled = False
    if "single_repro_method" not in st.session_state:
        st.session_state.single_repro_method = "LHS"
    if "single_repro_n_points" not in st.session_state:
        st.session_state.single_repro_n_points = 8
    if "single_repro_patterns" not in st.session_state:
        st.session_state.single_repro_patterns = ["Immediate (A->A)", "Bracketed (A->B->A)"]
    if "single_repro_use_sentinel" not in st.session_state:
        st.session_state.single_repro_use_sentinel = True
    if "single_repro_sentinel_every" not in st.session_state:
        st.session_state.single_repro_sentinel_every = 5
    if "single_repro_escalate_cleaning" not in st.session_state:
        st.session_state.single_repro_escalate_cleaning = True
    if "single_repro_max_cleaning" not in st.session_state:
        st.session_state.single_repro_max_cleaning = 2
    if "single_repro_block_on_fail" not in st.session_state:
        st.session_state.single_repro_block_on_fail = True

    # Multi-objective reproducibility defaults
    if "multi_repro_enabled" not in st.session_state:
        st.session_state.multi_repro_enabled = False
    if "multi_repro_method" not in st.session_state:
        st.session_state.multi_repro_method = "LHS"
    if "multi_repro_n_points" not in st.session_state:
        st.session_state.multi_repro_n_points = 8
    if "multi_repro_patterns" not in st.session_state:
        st.session_state.multi_repro_patterns = ["Immediate (A->A)", "Bracketed (A->B->A)"]
    if "multi_repro_use_sentinel" not in st.session_state:
        st.session_state.multi_repro_use_sentinel = True
    if "multi_repro_sentinel_every" not in st.session_state:
        st.session_state.multi_repro_sentinel_every = 5
    if "multi_repro_escalate_cleaning" not in st.session_state:
        st.session_state.multi_repro_escalate_cleaning = True
    if "multi_repro_max_cleaning" not in st.session_state:
        st.session_state.multi_repro_max_cleaning = 2
    if "multi_repro_block_on_fail" not in st.session_state:
        st.session_state.multi_repro_block_on_fail = True
    if "multi_repro_objective" not in st.session_state:
        st.session_state.multi_repro_objective = ""


ensure_defaults()

st.title("Reproducibility Studio")
st.caption("Configure reproducibility settings in one page and reuse them in Single/Multi optimization.")

section_header("Current Campaign Context", accent="#0ea5e9", background="#eff6ff")
variables = st.session_state.get("variables", [])
objectives = st.session_state.get("objectives", [])
single_obj = st.session_state.get("response_to_optimize", "")
col_ctx1, col_ctx2, col_ctx3 = st.columns(3)
col_ctx1.metric("Variables", len(variables))
col_ctx2.metric("Single Objective", single_obj if single_obj else "Not set")
col_ctx3.metric("Multi Objectives", len(objectives))
if variables:
    st.code(", ".join([str(v[0]) for v in variables]), language="text")
if objectives:
    st.code(", ".join([str(o) for o in objectives]), language="text")

section_header("Single-Objective Repro Settings", accent="#ef4444", background="#fef2f2")
st.checkbox("Enable gate for Single Objective", key="single_repro_enabled")
col_s1, col_s2, col_s3 = st.columns(3)
col_s1.selectbox("Method", ["LHS", "Corners + Center"], key="single_repro_method")
col_s2.number_input("Test points", min_value=2, max_value=64, step=1, key="single_repro_n_points")
col_s3.number_input("Sentinel every N runs", min_value=1, max_value=100, step=1, key="single_repro_sentinel_every")
st.multiselect(
    "Replication patterns",
    options=["Immediate (A->A)", "Bracketed (A->B->A)"],
    key="single_repro_patterns",
)
col_sf1, col_sf2, col_sf3, col_sf4 = st.columns(4)
col_sf1.checkbox("Enable sentinel", key="single_repro_use_sentinel")
col_sf2.checkbox("Escalate cleaning", key="single_repro_escalate_cleaning")
col_sf3.number_input("Max cleaning level", min_value=1, max_value=10, step=1, key="single_repro_max_cleaning")
col_sf4.checkbox("Block on FAIL", key="single_repro_block_on_fail")

section_header("Multi-Objective Repro Settings", accent="#f59e0b", background="#fffbeb")
st.checkbox("Enable gate for Multi Objective", key="multi_repro_enabled")
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.selectbox("Method", ["LHS", "Corners + Center"], key="multi_repro_method")
col_m2.number_input("Test points", min_value=2, max_value=64, step=1, key="multi_repro_n_points")
col_m3.number_input("Sentinel every N runs", min_value=1, max_value=100, step=1, key="multi_repro_sentinel_every")
st.multiselect(
    "Replication patterns",
    options=["Immediate (A->A)", "Bracketed (A->B->A)"],
    key="multi_repro_patterns",
)
if objectives:
    default_obj = st.session_state.get("multi_repro_objective", "")
    if default_obj not in objectives:
        default_obj = objectives[0]
    st.session_state.multi_repro_objective = st.selectbox(
        "Metric objective for multi reproducibility decision",
        options=objectives,
        index=objectives.index(default_obj),
    )
else:
    st.info("Set multi-objective targets in the Multi page, then come back to choose the metric objective.")
col_mf1, col_mf2, col_mf3, col_mf4 = st.columns(4)
col_mf1.checkbox("Enable sentinel", key="multi_repro_use_sentinel")
col_mf2.checkbox("Escalate cleaning", key="multi_repro_escalate_cleaning")
col_mf3.number_input("Max cleaning level", min_value=1, max_value=10, step=1, key="multi_repro_max_cleaning")
col_mf4.checkbox("Block on FAIL", key="multi_repro_block_on_fail")

section_header("Navigation", accent="#10b981", background="#ecfdf5")
st.info(
    "Reproducibility seed points are automatically imported into initialization. "
    "If additional initialization points are required, missing points are generated automatically."
)
col_go1, col_go2 = st.columns(2)
if col_go1.button("Go To Single Objective"):
    st.session_state.selected_page = "🎯 Autonomous Single Objective Optimization"
    st.rerun()
if col_go2.button("Go To Multi Objective"):
    st.session_state.selected_page = "🧭 Autonomous Multi-Objective Optimization"
    st.rerun()
