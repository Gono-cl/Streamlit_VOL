import streamlit as st

REPRO_CYCLIC_LABEL = "Cyclic (1->...->N) x repeats"
REPRO_CYCLIC_LABEL_LEGACY = "Cyclic (1->...->N)x3"
REPRO_PATTERN_LABELS = [
    "Immediate (A->A)",
    "Bracketed (A->B->A)",
    REPRO_CYCLIC_LABEL,
]
REPRO_PATTERN_LABEL_ALIASES = {
    "Immediate (A->A)": "Immediate (A->A)",
    "Bracketed (A->B->A)": "Bracketed (A->B->A)",
    REPRO_CYCLIC_LABEL: REPRO_CYCLIC_LABEL,
    REPRO_CYCLIC_LABEL_LEGACY: REPRO_CYCLIC_LABEL,
    "immediate": "Immediate (A->A)",
    "bracketed": "Bracketed (A->B->A)",
    "cyclic": REPRO_CYCLIC_LABEL,
}


def _normalize_pattern_selection(raw_value, fallback):
    if raw_value is None:
        return list(fallback)
    if isinstance(raw_value, (list, tuple, set)):
        raw_items = list(raw_value)
    else:
        raw_items = [raw_value]
    out = []
    for raw in raw_items:
        mapped = REPRO_PATTERN_LABEL_ALIASES.get(str(raw))
        if mapped and mapped not in out:
            out.append(mapped)
    if out:
        return out
    if len(raw_items) == 0:
        return []
    return list(fallback)


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
        st.session_state.single_repro_patterns = list(REPRO_PATTERN_LABELS[:2])
    if "single_repro_cyclic_repeats" not in st.session_state:
        st.session_state.single_repro_cyclic_repeats = 3
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
    if "single_repro_outlier_repeat_enabled" not in st.session_state:
        st.session_state.single_repro_outlier_repeat_enabled = False
    if "single_repro_outlier_pair_rsd_pct" not in st.session_state:
        st.session_state.single_repro_outlier_pair_rsd_pct = 5.0
    if "single_repro_outlier_gap_pct" not in st.session_state:
        st.session_state.single_repro_outlier_gap_pct = 10.0
    if "single_repro_outlier_downgrade_conditional" not in st.session_state:
        st.session_state.single_repro_outlier_downgrade_conditional = True

    # Multi-objective reproducibility defaults
    if "multi_repro_enabled" not in st.session_state:
        st.session_state.multi_repro_enabled = False
    if "multi_repro_method" not in st.session_state:
        st.session_state.multi_repro_method = "LHS"
    if "multi_repro_n_points" not in st.session_state:
        st.session_state.multi_repro_n_points = 8
    if "multi_repro_patterns" not in st.session_state:
        st.session_state.multi_repro_patterns = list(REPRO_PATTERN_LABELS[:2])
    if "multi_repro_cyclic_repeats" not in st.session_state:
        st.session_state.multi_repro_cyclic_repeats = 3
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
    if "multi_repro_outlier_repeat_enabled" not in st.session_state:
        st.session_state.multi_repro_outlier_repeat_enabled = False
    if "multi_repro_outlier_pair_rsd_pct" not in st.session_state:
        st.session_state.multi_repro_outlier_pair_rsd_pct = 5.0
    if "multi_repro_outlier_gap_pct" not in st.session_state:
        st.session_state.multi_repro_outlier_gap_pct = 10.0
    if "multi_repro_outlier_downgrade_conditional" not in st.session_state:
        st.session_state.multi_repro_outlier_downgrade_conditional = True


ensure_defaults()
st.session_state.single_repro_patterns = _normalize_pattern_selection(
    st.session_state.get("single_repro_patterns"),
    REPRO_PATTERN_LABELS[:2],
)
st.session_state.multi_repro_patterns = _normalize_pattern_selection(
    st.session_state.get("multi_repro_patterns"),
    REPRO_PATTERN_LABELS[:2],
)

# Keep Studio widgets decoupled from persistent config keys so values survive page switches.
if "single_repro_method_widget" not in st.session_state:
    st.session_state.single_repro_method_widget = st.session_state.get("single_repro_method", "LHS")
if "single_repro_n_points_widget" not in st.session_state:
    st.session_state.single_repro_n_points_widget = int(st.session_state.get("single_repro_n_points", 8))
if "single_repro_sentinel_every_widget" not in st.session_state:
    st.session_state.single_repro_sentinel_every_widget = int(st.session_state.get("single_repro_sentinel_every", 5))
if "single_repro_patterns_widget" not in st.session_state:
    st.session_state.single_repro_patterns_widget = list(
        st.session_state.get("single_repro_patterns", list(REPRO_PATTERN_LABELS[:2]))
    )
else:
    st.session_state.single_repro_patterns_widget = _normalize_pattern_selection(
        st.session_state.get("single_repro_patterns_widget"),
        st.session_state.get("single_repro_patterns", REPRO_PATTERN_LABELS[:2]),
    )
if "single_repro_use_sentinel_widget" not in st.session_state:
    st.session_state.single_repro_use_sentinel_widget = bool(st.session_state.get("single_repro_use_sentinel", True))
if "single_repro_cyclic_repeats_widget" not in st.session_state:
    st.session_state.single_repro_cyclic_repeats_widget = int(st.session_state.get("single_repro_cyclic_repeats", 3))
if "single_repro_escalate_cleaning_widget" not in st.session_state:
    st.session_state.single_repro_escalate_cleaning_widget = bool(st.session_state.get("single_repro_escalate_cleaning", True))
if "single_repro_max_cleaning_widget" not in st.session_state:
    st.session_state.single_repro_max_cleaning_widget = int(st.session_state.get("single_repro_max_cleaning", 2))
if "single_repro_block_on_fail_widget" not in st.session_state:
    st.session_state.single_repro_block_on_fail_widget = bool(st.session_state.get("single_repro_block_on_fail", True))
if "single_repro_outlier_repeat_enabled_widget" not in st.session_state:
    st.session_state.single_repro_outlier_repeat_enabled_widget = bool(
        st.session_state.get("single_repro_outlier_repeat_enabled", False)
    )
if "single_repro_outlier_pair_rsd_pct_widget" not in st.session_state:
    st.session_state.single_repro_outlier_pair_rsd_pct_widget = float(
        st.session_state.get("single_repro_outlier_pair_rsd_pct", 5.0)
    )
if "single_repro_outlier_gap_pct_widget" not in st.session_state:
    st.session_state.single_repro_outlier_gap_pct_widget = float(
        st.session_state.get("single_repro_outlier_gap_pct", 10.0)
    )
if "single_repro_outlier_downgrade_conditional_widget" not in st.session_state:
    st.session_state.single_repro_outlier_downgrade_conditional_widget = bool(
        st.session_state.get("single_repro_outlier_downgrade_conditional", True)
    )

if "multi_repro_method_widget" not in st.session_state:
    st.session_state.multi_repro_method_widget = st.session_state.get("multi_repro_method", "LHS")
if "multi_repro_n_points_widget" not in st.session_state:
    st.session_state.multi_repro_n_points_widget = int(st.session_state.get("multi_repro_n_points", 8))
if "multi_repro_sentinel_every_widget" not in st.session_state:
    st.session_state.multi_repro_sentinel_every_widget = int(st.session_state.get("multi_repro_sentinel_every", 5))
if "multi_repro_patterns_widget" not in st.session_state:
    st.session_state.multi_repro_patterns_widget = list(
        st.session_state.get("multi_repro_patterns", list(REPRO_PATTERN_LABELS[:2]))
    )
else:
    st.session_state.multi_repro_patterns_widget = _normalize_pattern_selection(
        st.session_state.get("multi_repro_patterns_widget"),
        st.session_state.get("multi_repro_patterns", REPRO_PATTERN_LABELS[:2]),
    )
if "multi_repro_use_sentinel_widget" not in st.session_state:
    st.session_state.multi_repro_use_sentinel_widget = bool(st.session_state.get("multi_repro_use_sentinel", True))
if "multi_repro_cyclic_repeats_widget" not in st.session_state:
    st.session_state.multi_repro_cyclic_repeats_widget = int(st.session_state.get("multi_repro_cyclic_repeats", 3))
if "multi_repro_escalate_cleaning_widget" not in st.session_state:
    st.session_state.multi_repro_escalate_cleaning_widget = bool(st.session_state.get("multi_repro_escalate_cleaning", True))
if "multi_repro_max_cleaning_widget" not in st.session_state:
    st.session_state.multi_repro_max_cleaning_widget = int(st.session_state.get("multi_repro_max_cleaning", 2))
if "multi_repro_block_on_fail_widget" not in st.session_state:
    st.session_state.multi_repro_block_on_fail_widget = bool(st.session_state.get("multi_repro_block_on_fail", True))
if "multi_repro_objective_widget" not in st.session_state:
    st.session_state.multi_repro_objective_widget = st.session_state.get("multi_repro_objective", "")
if "multi_repro_outlier_repeat_enabled_widget" not in st.session_state:
    st.session_state.multi_repro_outlier_repeat_enabled_widget = bool(
        st.session_state.get("multi_repro_outlier_repeat_enabled", False)
    )
if "multi_repro_outlier_pair_rsd_pct_widget" not in st.session_state:
    st.session_state.multi_repro_outlier_pair_rsd_pct_widget = float(
        st.session_state.get("multi_repro_outlier_pair_rsd_pct", 5.0)
    )
if "multi_repro_outlier_gap_pct_widget" not in st.session_state:
    st.session_state.multi_repro_outlier_gap_pct_widget = float(
        st.session_state.get("multi_repro_outlier_gap_pct", 10.0)
    )
if "multi_repro_outlier_downgrade_conditional_widget" not in st.session_state:
    st.session_state.multi_repro_outlier_downgrade_conditional_widget = bool(
        st.session_state.get("multi_repro_outlier_downgrade_conditional", True)
    )

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
single_method = col_s1.selectbox("Method", ["LHS", "Corners + Center"], key="single_repro_method_widget")
single_n_points = col_s2.number_input("Test points", min_value=2, max_value=64, step=1, key="single_repro_n_points_widget")
single_sentinel_every = col_s3.number_input("Sentinel every N runs", min_value=1, max_value=100, step=1, key="single_repro_sentinel_every_widget")
single_patterns = st.multiselect(
    "Replication patterns",
    options=REPRO_PATTERN_LABELS,
    key="single_repro_patterns_widget",
)
single_cyclic_repeats = st.number_input(
    "Cyclic repeats",
    min_value=3,
    max_value=20,
    step=1,
    key="single_repro_cyclic_repeats_widget",
)
col_sf1, col_sf2, col_sf3, col_sf4 = st.columns(4)
single_use_sentinel = col_sf1.checkbox("Enable sentinel", key="single_repro_use_sentinel_widget")
single_escalate = col_sf2.checkbox("Escalate cleaning", key="single_repro_escalate_cleaning_widget")
single_max_cleaning = col_sf3.number_input("Max cleaning level", min_value=1, max_value=10, step=1, key="single_repro_max_cleaning_widget")
single_block_on_fail = col_sf4.checkbox("Block on FAIL", key="single_repro_block_on_fail_widget")
st.caption("Optional cyclic adjudication: if one cyclic result is far from the remaining cluster, run one extra full replicate for that point.")
col_so1, col_so2, col_so3, col_so4 = st.columns(4)
single_outlier_repeat = col_so1.checkbox("Repeat suspicious cyclic points", key="single_repro_outlier_repeat_enabled_widget")
single_outlier_pair_rsd = col_so2.number_input(
    "Pair max RSD (%)",
    min_value=0.1,
    max_value=100.0,
    value=float(st.session_state.get("single_repro_outlier_pair_rsd_pct_widget", 5.0)),
    step=0.5,
    key="single_repro_outlier_pair_rsd_pct_widget",
)
single_outlier_gap = col_so3.number_input(
    "Outlier min gap (%)",
    min_value=0.1,
    max_value=500.0,
    value=float(st.session_state.get("single_repro_outlier_gap_pct_widget", 10.0)),
    step=1.0,
    key="single_repro_outlier_gap_pct_widget",
)
single_outlier_downgrade = col_so4.checkbox(
    "Downgrade resolved cases to CONDITIONAL",
    key="single_repro_outlier_downgrade_conditional_widget",
)

st.session_state.single_repro_method = str(single_method)
st.session_state.single_repro_n_points = int(single_n_points)
st.session_state.single_repro_sentinel_every = int(single_sentinel_every)
st.session_state.single_repro_patterns = list(single_patterns)
st.session_state.single_repro_cyclic_repeats = int(single_cyclic_repeats)
st.session_state.single_repro_use_sentinel = bool(single_use_sentinel)
st.session_state.single_repro_escalate_cleaning = bool(single_escalate)
st.session_state.single_repro_max_cleaning = int(single_max_cleaning)
st.session_state.single_repro_block_on_fail = bool(single_block_on_fail)
st.session_state.single_repro_outlier_repeat_enabled = bool(single_outlier_repeat)
st.session_state.single_repro_outlier_pair_rsd_pct = float(single_outlier_pair_rsd)
st.session_state.single_repro_outlier_gap_pct = float(single_outlier_gap)
st.session_state.single_repro_outlier_downgrade_conditional = bool(single_outlier_downgrade)

section_header("Multi-Objective Repro Settings", accent="#f59e0b", background="#fffbeb")
st.checkbox("Enable gate for Multi Objective", key="multi_repro_enabled")
col_m1, col_m2, col_m3 = st.columns(3)
multi_method = col_m1.selectbox("Method", ["LHS", "Corners + Center"], key="multi_repro_method_widget")
multi_n_points = col_m2.number_input("Test points", min_value=2, max_value=64, step=1, key="multi_repro_n_points_widget")
multi_sentinel_every = col_m3.number_input("Sentinel every N runs", min_value=1, max_value=100, step=1, key="multi_repro_sentinel_every_widget")
multi_patterns = st.multiselect(
    "Replication patterns",
    options=REPRO_PATTERN_LABELS,
    key="multi_repro_patterns_widget",
)
multi_cyclic_repeats = st.number_input(
    "Cyclic repeats",
    min_value=3,
    max_value=20,
    step=1,
    key="multi_repro_cyclic_repeats_widget",
)
if objectives:
    default_obj = st.session_state.get("multi_repro_objective_widget", st.session_state.get("multi_repro_objective", ""))
    if default_obj not in objectives:
        default_obj = objectives[0]
    selected_multi_obj = st.selectbox(
        "Metric objective for multi reproducibility decision",
        options=objectives,
        index=objectives.index(default_obj),
        key="multi_repro_objective_widget",
    )
    st.session_state.multi_repro_objective = str(selected_multi_obj)
else:
    st.info("Set multi-objective targets in the Multi page, then come back to choose the metric objective.")
col_mf1, col_mf2, col_mf3, col_mf4 = st.columns(4)
multi_use_sentinel = col_mf1.checkbox("Enable sentinel", key="multi_repro_use_sentinel_widget")
multi_escalate = col_mf2.checkbox("Escalate cleaning", key="multi_repro_escalate_cleaning_widget")
multi_max_cleaning = col_mf3.number_input("Max cleaning level", min_value=1, max_value=10, step=1, key="multi_repro_max_cleaning_widget")
multi_block_on_fail = col_mf4.checkbox("Block on FAIL", key="multi_repro_block_on_fail_widget")
st.caption("Optional cyclic adjudication: if one cyclic result is far from the remaining cluster, run one extra full replicate for that point.")
col_mo1, col_mo2, col_mo3, col_mo4 = st.columns(4)
multi_outlier_repeat = col_mo1.checkbox("Repeat suspicious cyclic points", key="multi_repro_outlier_repeat_enabled_widget")
multi_outlier_pair_rsd = col_mo2.number_input(
    "Pair max RSD (%)",
    min_value=0.1,
    max_value=100.0,
    value=float(st.session_state.get("multi_repro_outlier_pair_rsd_pct_widget", 5.0)),
    step=0.5,
    key="multi_repro_outlier_pair_rsd_pct_widget",
)
multi_outlier_gap = col_mo3.number_input(
    "Outlier min gap (%)",
    min_value=0.1,
    max_value=500.0,
    value=float(st.session_state.get("multi_repro_outlier_gap_pct_widget", 10.0)),
    step=1.0,
    key="multi_repro_outlier_gap_pct_widget",
)
multi_outlier_downgrade = col_mo4.checkbox(
    "Downgrade resolved cases to CONDITIONAL",
    key="multi_repro_outlier_downgrade_conditional_widget",
)

st.session_state.multi_repro_method = str(multi_method)
st.session_state.multi_repro_n_points = int(multi_n_points)
st.session_state.multi_repro_sentinel_every = int(multi_sentinel_every)
st.session_state.multi_repro_patterns = list(multi_patterns)
st.session_state.multi_repro_cyclic_repeats = int(multi_cyclic_repeats)
st.session_state.multi_repro_use_sentinel = bool(multi_use_sentinel)
st.session_state.multi_repro_escalate_cleaning = bool(multi_escalate)
st.session_state.multi_repro_max_cleaning = int(multi_max_cleaning)
st.session_state.multi_repro_block_on_fail = bool(multi_block_on_fail)
st.session_state.multi_repro_outlier_repeat_enabled = bool(multi_outlier_repeat)
st.session_state.multi_repro_outlier_pair_rsd_pct = float(multi_outlier_pair_rsd)
st.session_state.multi_repro_outlier_gap_pct = float(multi_outlier_gap)
st.session_state.multi_repro_outlier_downgrade_conditional = bool(multi_outlier_downgrade)

section_header("Navigation", accent="#10b981", background="#ecfdf5")
st.info(
    "Reproducibility seed points are automatically imported into initialization. "
    "If additional initialization points are required, missing points are generated automatically."
)
col_go1, col_go2 = st.columns(2)
if col_go1.button("Go To Single Objective"):
    st.session_state["_single_repro_from_studio"] = {
        "single_repro_enabled": bool(st.session_state.get("single_repro_enabled", False)),
        "single_repro_method": st.session_state.get("single_repro_method", "LHS"),
        "single_repro_n_points": int(st.session_state.get("single_repro_n_points", 8)),
        "single_repro_patterns": list(st.session_state.get("single_repro_patterns", list(REPRO_PATTERN_LABELS[:2]))),
        "single_repro_cyclic_repeats": int(st.session_state.get("single_repro_cyclic_repeats", 3)),
        "single_repro_use_sentinel": bool(st.session_state.get("single_repro_use_sentinel", True)),
        "single_repro_sentinel_every": int(st.session_state.get("single_repro_sentinel_every", 5)),
        "single_repro_escalate_cleaning": bool(st.session_state.get("single_repro_escalate_cleaning", True)),
        "single_repro_max_cleaning": int(st.session_state.get("single_repro_max_cleaning", 2)),
        "single_repro_block_on_fail": bool(st.session_state.get("single_repro_block_on_fail", True)),
        "single_repro_outlier_repeat_enabled": bool(st.session_state.get("single_repro_outlier_repeat_enabled", False)),
        "single_repro_outlier_pair_rsd_pct": float(st.session_state.get("single_repro_outlier_pair_rsd_pct", 5.0)),
        "single_repro_outlier_gap_pct": float(st.session_state.get("single_repro_outlier_gap_pct", 10.0)),
        "single_repro_outlier_downgrade_conditional": bool(st.session_state.get("single_repro_outlier_downgrade_conditional", True)),
    }
    st.session_state.single_ui_mode = "Advanced"
    st.session_state.selected_page = "single_objective"
    st.rerun()
if col_go2.button("Go To Multi Objective"):
    st.session_state["_multi_repro_from_studio"] = {
        "multi_repro_enabled": bool(st.session_state.get("multi_repro_enabled", False)),
        "multi_repro_method": st.session_state.get("multi_repro_method", "LHS"),
        "multi_repro_n_points": int(st.session_state.get("multi_repro_n_points", 8)),
        "multi_repro_patterns": list(st.session_state.get("multi_repro_patterns", list(REPRO_PATTERN_LABELS[:2]))),
        "multi_repro_cyclic_repeats": int(st.session_state.get("multi_repro_cyclic_repeats", 3)),
        "multi_repro_use_sentinel": bool(st.session_state.get("multi_repro_use_sentinel", True)),
        "multi_repro_sentinel_every": int(st.session_state.get("multi_repro_sentinel_every", 5)),
        "multi_repro_escalate_cleaning": bool(st.session_state.get("multi_repro_escalate_cleaning", True)),
        "multi_repro_max_cleaning": int(st.session_state.get("multi_repro_max_cleaning", 2)),
        "multi_repro_block_on_fail": bool(st.session_state.get("multi_repro_block_on_fail", True)),
        "multi_repro_objective": st.session_state.get("multi_repro_objective", ""),
        "multi_repro_outlier_repeat_enabled": bool(st.session_state.get("multi_repro_outlier_repeat_enabled", False)),
        "multi_repro_outlier_pair_rsd_pct": float(st.session_state.get("multi_repro_outlier_pair_rsd_pct", 5.0)),
        "multi_repro_outlier_gap_pct": float(st.session_state.get("multi_repro_outlier_gap_pct", 10.0)),
        "multi_repro_outlier_downgrade_conditional": bool(st.session_state.get("multi_repro_outlier_downgrade_conditional", True)),
    }
    st.session_state.multi_ui_mode = "Advanced"
    st.session_state.selected_page = "multi_objective"
    st.rerun()
