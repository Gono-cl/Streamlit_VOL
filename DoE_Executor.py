import json
import os
import sys
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st

from core.campaigns import MULTI_OBJECTIVE_OPTIONS, SINGLE_OBJECTIVE_OPTIONS
from core.hardware.experimental_run import ExperimentRunner
from core.hardware.opc_communication import OPCClient
from core.hardware.process_adapters import DEFAULT_PROCESS_ADAPTER
from core.hardware.protocol_scripts import (
    list_protocol_scripts,
    load_protocol_module,
    protocol_required_parameter_keys,
)
from core.utils import db_handler
from core.utils.export_tools import export_to_csv, export_to_excel
from core.utils.logger import StreamlitLogger


SAVE_DIR = "resumable_doe_executor_runs"
os.makedirs(SAVE_DIR, exist_ok=True)


def sanitize_filename(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    cleaned = name.strip().rstrip(".")
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        cleaned = cleaned.replace(ch, "_")
    cleaned = " ".join(cleaned.split())
    return cleaned or "run"


def ensure_session_defaults():
    if "simulation_mode" not in st.session_state:
        st.session_state.simulation_mode = "off"
    if "opc_url" not in st.session_state:
        st.session_state.opc_url = "http://em-nun:57080"
    if "use_autosampler" not in st.session_state:
        st.session_state.use_autosampler = False
    if "volume_to_collect" not in st.session_state:
        st.session_state.volume_to_collect = 3.0
    if "process_adapter" not in st.session_state:
        st.session_state.process_adapter = DEFAULT_PROCESS_ADAPTER
    if "process_adapter_config" not in st.session_state:
        st.session_state.process_adapter_config = {}
    if "running_protocol_script" not in st.session_state:
        st.session_state.running_protocol_script = None

    if "doe_exec_upload_sig" not in st.session_state:
        st.session_state.doe_exec_upload_sig = ""
    if "doe_exec_plan_df" not in st.session_state:
        st.session_state.doe_exec_plan_df = pd.DataFrame()
    if "doe_exec_param_cols" not in st.session_state:
        st.session_state.doe_exec_param_cols = []
    if "doe_exec_objectives" not in st.session_state:
        st.session_state.doe_exec_objectives = []
    if "doe_exec_param_cols_select" not in st.session_state:
        st.session_state.doe_exec_param_cols_select = []
    if "doe_exec_objectives_select" not in st.session_state:
        st.session_state.doe_exec_objectives_select = []
    if "doe_exec_results" not in st.session_state:
        st.session_state.doe_exec_results = []
    if "doe_exec_queue" not in st.session_state:
        st.session_state.doe_exec_queue = []
    if "doe_exec_running" not in st.session_state:
        st.session_state.doe_exec_running = False
    if "doe_exec_stop_requested" not in st.session_state:
        st.session_state.doe_exec_stop_requested = False
    if "doe_exec_runner" not in st.session_state:
        st.session_state.doe_exec_runner = None
    if "doe_exec_run_name" not in st.session_state:
        st.session_state.doe_exec_run_name = datetime.now().strftime("doe_exec_%Y%m%d_%H%M%S")
    if "doe_exec_notes" not in st.session_state:
        st.session_state.doe_exec_notes = ""
    if "doe_exec_date" not in st.session_state:
        st.session_state.doe_exec_date = datetime.today().date()
    if "doe_exec_loaded_file_name" not in st.session_state:
        st.session_state.doe_exec_loaded_file_name = ""
    if "doe_exec_db_saved" not in st.session_state:
        st.session_state.doe_exec_db_saved = False


def read_matrix_file(uploaded_file) -> pd.DataFrame:
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    if suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(uploaded_file)
    else:
        df = pd.read_csv(uploaded_file)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def default_template_value(param_key: str) -> float:
    k = str(param_key).lower()
    if "flow" in k:
        return 1.0
    if "voltage" in k:
        return 20.0
    if "residence" in k:
        return 20.0
    if "temperature" in k:
        return 25.0
    if "pressure" in k:
        return 1.0
    if "concentration" in k or "conc" in k:
        return 300.0
    return 1.0


def build_doe_template_df(required_keys: list[str]) -> pd.DataFrame:
    row = {"run_id": 1}
    for key in required_keys:
        row[key] = default_template_value(key)
    row["objectives"] = "Yield"
    return pd.DataFrame([row])


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="DOE_Matrix")
    return output.getvalue()


def status_counts(plan_df: pd.DataFrame) -> dict:
    if plan_df is None or plan_df.empty or "__status" not in plan_df.columns:
        return {"pending": 0, "running": 0, "done": 0, "failed": 0}
    counts = plan_df["__status"].value_counts().to_dict()
    return {
        "pending": int(counts.get("pending", 0)),
        "running": int(counts.get("running", 0)),
        "done": int(counts.get("done", 0)),
        "failed": int(counts.get("failed", 0)),
    }


def objective_catalog(extra_objectives=None):
    extras = list(extra_objectives or [])
    catalog = []
    for item in list(SINGLE_OBJECTIVE_OPTIONS) + list(MULTI_OBJECTIVE_OPTIONS) + extras:
        if item and item not in catalog:
            catalog.append(item)
    return catalog


def detect_objectives(df: pd.DataFrame):
    catalog = objective_catalog()
    by_columns = [c for c in df.columns if c in catalog]

    by_field = []
    objective_field = None
    for candidate in ["objectives", "objective"]:
        if candidate in df.columns:
            objective_field = candidate
            break
    if objective_field:
        tokens = []
        series = df[objective_field].dropna()
        for val in series.tolist():
            text = str(val).strip()
            if not text:
                continue
            for chunk in text.replace(";", ",").replace("|", ",").split(","):
                t = chunk.strip()
                if t:
                    tokens.append(t)
        for obj in objective_catalog(tokens):
            if obj in tokens:
                by_field.append(obj)

    detected = []
    for obj in by_field + by_columns:
        if obj not in detected:
            detected.append(obj)
    return detected


def infer_parameter_columns(df: pd.DataFrame, detected_objectives: list[str]):
    reserved = {
        "Experiment #",
        "Timestamp",
        "Measurement",
        "Run",
        "Status",
        "status",
        "run_id",
        "objective",
        "objectives",
        "__status",
        "__error",
    }
    reserved.update(set(objective_catalog()))
    reserved.update(set(detected_objectives))

    candidates = []
    for col in df.columns:
        if col in reserved:
            continue
        series = df[col]
        if series.dropna().empty:
            continue
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() >= max(1, len(series.dropna())):
            candidates.append(col)
    return candidates


def ensure_plan_columns(plan_df: pd.DataFrame) -> pd.DataFrame:
    df = plan_df.copy()
    if "__status" not in df.columns:
        df["__status"] = "pending"
    else:
        df["__status"] = df["__status"].fillna("pending").astype(str)
    if "__error" not in df.columns:
        df["__error"] = ""
    else:
        df["__error"] = df["__error"].fillna("").astype(str)
    invalid_status = ~df["__status"].isin(["pending", "running", "done", "failed"])
    df.loc[invalid_status, "__status"] = "pending"
    df.loc[df["__status"] == "running", "__status"] = "pending"
    return df


def build_variable_bounds(plan_df: pd.DataFrame, param_cols: list[str]):
    variables = []
    for col in param_cols:
        numeric = pd.to_numeric(plan_df[col], errors="coerce")
        valid = numeric.dropna()
        if valid.empty:
            low = 0.0
            high = 0.0
        else:
            low = float(valid.min())
            high = float(valid.max())
        variables.append((col, low, high, ""))
    return variables


def persist_run_state(run_path: str, plan_df: pd.DataFrame, results_df: pd.DataFrame, metadata: dict):
    os.makedirs(run_path, exist_ok=True)
    plan_df.to_csv(os.path.join(run_path, "input_plan.csv"), index=False)
    results_df.to_csv(os.path.join(run_path, "experiment_data.csv"), index=False)
    with open(os.path.join(run_path, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, default=str)


def make_metadata(run_name: str, notes: str, run_date, param_cols: list[str], objectives: list[str]):
    return {
        "mode": "doe_executor",
        "run_name": run_name,
        "notes": notes,
        "date": str(run_date),
        "parameter_columns": list(param_cols),
        "objectives": list(objectives),
        "simulation_mode": st.session_state.get("simulation_mode", "off"),
        "opc_url": st.session_state.get("opc_url", "http://em-nun:57080"),
        "use_autosampler": bool(st.session_state.get("use_autosampler", False)),
        "volume_to_collect": float(st.session_state.get("volume_to_collect", 3.0)),
        "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
        "process_adapter_config": st.session_state.get("process_adapter_config", {}),
        "running_protocol_script": st.session_state.get("running_protocol_script"),
        "source_file": st.session_state.get("doe_exec_loaded_file_name", ""),
        "total_rows": int(len(st.session_state.get("doe_exec_plan_df", pd.DataFrame()))),
    }


def load_saved_run(run_name: str):
    run_path = os.path.join(SAVE_DIR, run_name)
    plan_path = os.path.join(run_path, "input_plan.csv")
    results_path = os.path.join(run_path, "experiment_data.csv")
    meta_path = os.path.join(run_path, "metadata.json")
    if not os.path.exists(plan_path) or not os.path.exists(meta_path):
        st.error("Selected run does not contain the expected files.")
        return

    plan_df = pd.read_csv(plan_path)
    plan_df = ensure_plan_columns(plan_df)
    results_df = pd.read_csv(results_path) if os.path.exists(results_path) else pd.DataFrame()
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    st.session_state.doe_exec_plan_df = plan_df
    st.session_state.doe_exec_results = results_df.to_dict("records")
    st.session_state.doe_exec_param_cols = list(metadata.get("parameter_columns", []))
    st.session_state.doe_exec_objectives = list(metadata.get("objectives", []))
    st.session_state.doe_exec_run_name = sanitize_filename(metadata.get("run_name", run_name))
    st.session_state.doe_exec_notes = metadata.get("notes", "")
    st.session_state.doe_exec_loaded_file_name = metadata.get("source_file", "")
    st.session_state.doe_exec_upload_sig = ""
    st.session_state.doe_exec_running = False
    st.session_state.doe_exec_stop_requested = False
    st.session_state.doe_exec_queue = []
    st.session_state.doe_exec_db_saved = False
    try:
        st.session_state.doe_exec_date = datetime.fromisoformat(str(metadata.get("date", ""))).date()
    except Exception:
        pass
    st.session_state.doe_exec_param_cols_select = list(st.session_state.doe_exec_param_cols)
    st.session_state.doe_exec_objectives_select = list(st.session_state.doe_exec_objectives)

    st.session_state.simulation_mode = metadata.get("simulation_mode", st.session_state.get("simulation_mode", "off"))
    st.session_state.opc_url = metadata.get("opc_url", st.session_state.get("opc_url", "http://em-nun:57080"))
    st.session_state.use_autosampler = bool(metadata.get("use_autosampler", st.session_state.get("use_autosampler", False)))
    st.session_state.volume_to_collect = float(metadata.get("volume_to_collect", st.session_state.get("volume_to_collect", 3.0)))
    st.session_state.process_adapter = metadata.get("process_adapter", st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER))
    st.session_state.process_adapter_config = metadata.get("process_adapter_config", st.session_state.get("process_adapter_config", {}))
    st.session_state.running_protocol_script = metadata.get("running_protocol_script", st.session_state.get("running_protocol_script"))
    st.success(f"Loaded run: {run_name}")


ensure_session_defaults()

st.title("DOE Executor")
st.caption("Upload an externally created run matrix and execute it directly. No optimizer is used.")

# Sidebar hardware controls
sim_mode_label = {
    "off": "Real Hardware (Full)",
    "hybrid": "Hybrid (Simulated Measurement)",
    "full": "Full Simulation (No Hardware)",
}
sim_modes = ["off", "hybrid", "full"]
sim_default = st.session_state.get("simulation_mode", "off")
if sim_default not in sim_modes:
    sim_default = "off"
simulation_mode = st.sidebar.selectbox(
    "Experiment Mode",
    options=sim_modes,
    index=sim_modes.index(sim_default),
    format_func=lambda x: sim_mode_label[x],
    key="doe_exec_sim_mode",
)
st.session_state.simulation_mode = simulation_mode

opc_url = st.sidebar.text_input(
    "OPC Server URL",
    value=st.session_state.get("opc_url", "http://em-nun:57080"),
    key="doe_exec_opc_url",
)
st.session_state.opc_url = opc_url

use_autosampler = st.sidebar.checkbox(
    "Use Autosampler",
    value=bool(st.session_state.get("use_autosampler", False)),
    key="doe_exec_use_autosampler",
)
st.session_state.use_autosampler = use_autosampler

volume_to_collect = st.sidebar.number_input(
    "Desired volume (ml)",
    min_value=0.0,
    max_value=6.0,
    value=float(st.session_state.get("volume_to_collect", 3.0)),
    step=0.5,
    key="doe_exec_volume_collect",
)
st.session_state.volume_to_collect = volume_to_collect

protocol_script_options = ["None"] + list_protocol_scripts()
protocol_script_default = st.session_state.get("running_protocol_script", "None") or "None"
if protocol_script_default not in protocol_script_options:
    protocol_script_default = "None"
selected_protocol_script = st.sidebar.selectbox(
    "Running Protocol File",
    options=protocol_script_options,
    index=protocol_script_options.index(protocol_script_default),
    key="doe_exec_protocol_script",
)
st.session_state.running_protocol_script = None if selected_protocol_script == "None" else selected_protocol_script
if st.session_state.running_protocol_script:
    try:
        load_protocol_module(st.session_state.running_protocol_script)
        st.sidebar.caption(f"Using protocol: `{st.session_state.running_protocol_script}`")
    except Exception as exc:
        st.sidebar.error(f"Protocol load error: {exc}")
if simulation_mode != "off":
    st.warning("Simulation mode is enabled. Hardware actions are partially or fully bypassed.")

st.sidebar.markdown("---")
saved_runs = [d for d in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, d))]
selected_saved = st.sidebar.selectbox("Load DOE Executor Run", options=["None"] + sorted(saved_runs), key="doe_exec_saved_run")
if selected_saved != "None" and st.sidebar.button("Load Saved DOE Run", key="doe_exec_load_saved_run"):
    load_saved_run(selected_saved)

# Metadata
st.subheader("Run Metadata")
default_name = st.session_state.get("doe_exec_run_name", datetime.now().strftime("doe_exec_%Y%m%d_%H%M%S"))
run_name_raw = st.text_input("Run Name", value=default_name)
run_name = sanitize_filename(run_name_raw)
if run_name != run_name_raw:
    st.info(f"Adjusted run name to '{run_name}' for filesystem compatibility.")
st.session_state.doe_exec_run_name = run_name

run_date = st.date_input("Run Date", value=st.session_state.get("doe_exec_date", datetime.today().date()))
st.session_state.doe_exec_date = run_date
notes = st.text_area("Notes", value=st.session_state.get("doe_exec_notes", ""))
st.session_state.doe_exec_notes = notes

# Matrix upload and setup
required_keys = []
if st.session_state.get("running_protocol_script"):
    try:
        required_keys = protocol_required_parameter_keys(st.session_state["running_protocol_script"])
    except Exception as exc:
        st.error(f"Could not read required keys from protocol script: {exc}")
        required_keys = []

with st.expander("How To Use This Page", expanded=False):
    st.markdown(
        "\n".join(
            [
                "1. Set hardware mode and choose a running protocol file in the sidebar.",
                "2. Download the template and prepare your external DOE matrix.",
                "3. Upload the matrix (CSV/XLSX).",
                "4. Confirm `Parameter columns` and `Objectives to measure`.",
                "5. Start execution with `Start From Pending`.",
                "6. Use `Retry Failed` to rerun only failed rows, or `Resume All Open` for pending + failed rows.",
                "7. Export results and optionally save to the experiment database.",
            ]
        )
    )
    st.write("Required parameter columns from the selected running protocol:")
    if required_keys:
        st.code(", ".join(required_keys), language="text")
    else:
        st.code("No required keys defined (or no protocol selected).", language="text")
    st.caption(
        "Template columns: `run_id`, required parameter keys, and optional `objectives` "
        "(comma-separated, e.g. `Yield, Throughput`)."
    )
    st.caption(
        "Internal columns `__status` and `__error` are managed automatically to support stop/resume/retry."
    )

st.subheader("DOE Matrix Upload")
template_df = build_doe_template_df(required_keys)
st.caption("Download a template aligned to the currently selected running protocol.")
tpl_col_csv, tpl_col_xlsx = st.columns(2)
with tpl_col_csv:
    st.download_button(
        label="Download Template CSV",
        data=template_df.to_csv(index=False).encode("utf-8"),
        file_name="doe_executor_template.csv",
        mime="text/csv",
        key="doe_exec_download_template_csv",
    )
with tpl_col_xlsx:
    st.download_button(
        label="Download Template XLSX",
        data=dataframe_to_excel_bytes(template_df),
        file_name="doe_executor_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="doe_exec_download_template_xlsx",
    )

uploaded_matrix = st.file_uploader("Upload predefined matrix (CSV/XLSX)", type=["csv", "xlsx", "xls"], key="doe_exec_file")
if uploaded_matrix is not None:
    upload_sig = f"{uploaded_matrix.name}:{uploaded_matrix.size}"
    if st.session_state.get("doe_exec_upload_sig") != upload_sig:
        try:
            uploaded_df = read_matrix_file(uploaded_matrix)
        except Exception as exc:
            st.error(f"Could not read uploaded file: {exc}")
            uploaded_df = None

        if uploaded_df is not None:
            if uploaded_df.empty:
                st.error("Uploaded file has no rows.")
            else:
                plan_df = uploaded_df.copy()
                plan_df = ensure_plan_columns(plan_df)
                detected_objs = detect_objectives(plan_df)
                default_param_cols = infer_parameter_columns(plan_df, detected_objs)
                default_objectives = detected_objs if detected_objs else ["Yield"]

                st.session_state.doe_exec_plan_df = plan_df
                st.session_state.doe_exec_param_cols = default_param_cols
                st.session_state.doe_exec_objectives = default_objectives
                st.session_state.doe_exec_param_cols_select = list(default_param_cols)
                st.session_state.doe_exec_objectives_select = list(default_objectives)
                st.session_state.doe_exec_results = []
                st.session_state.doe_exec_queue = []
                st.session_state.doe_exec_running = False
                st.session_state.doe_exec_stop_requested = False
                st.session_state.doe_exec_upload_sig = upload_sig
                st.session_state.doe_exec_loaded_file_name = uploaded_matrix.name
                st.session_state.doe_exec_db_saved = False

if not st.session_state.doe_exec_plan_df.empty:
    plan_df = ensure_plan_columns(st.session_state.doe_exec_plan_df)
    st.session_state.doe_exec_plan_df = plan_df
    base_columns = [c for c in plan_df.columns if c not in ["__status", "__error"]]
    detected_objs = detect_objectives(plan_df)
    catalog = objective_catalog(detected_objs)

    st.session_state.doe_exec_param_cols_select = [
        c for c in st.session_state.get("doe_exec_param_cols_select", st.session_state.get("doe_exec_param_cols", []))
        if c in base_columns
    ]
    st.session_state.doe_exec_objectives_select = [
        o for o in st.session_state.get("doe_exec_objectives_select", st.session_state.get("doe_exec_objectives", []))
        if o in catalog
    ]

    st.caption(f"Loaded matrix: `{st.session_state.get('doe_exec_loaded_file_name', 'in-memory')}`")
    st.session_state.doe_exec_param_cols = st.multiselect(
        "Parameter columns",
        options=base_columns,
        key="doe_exec_param_cols_select",
    )
    st.session_state.doe_exec_objectives = st.multiselect(
        "Objectives to measure",
        options=catalog,
        key="doe_exec_objectives_select",
    )

    st.markdown("### Matrix Preview")
    st.dataframe(plan_df, use_container_width=True)

    counts = status_counts(plan_df)
    st.write(
        f"Rows: {len(plan_df)} | pending: {counts['pending']} | "
        f"running: {counts['running']} | done: {counts['done']} | failed: {counts['failed']}"
    )
else:
    st.info("Upload a DOE matrix to start.")

selected_param_cols = st.session_state.get("doe_exec_param_cols", [])
missing_required_cols = [k for k in required_keys if k not in selected_param_cols]

if missing_required_cols and not st.session_state.doe_exec_plan_df.empty:
    st.error(
        "The selected parameter columns do not satisfy the running protocol requirements. "
        f"Missing: {missing_required_cols}"
    )

st.subheader("Execution Control")
col_start, col_resume, col_retry, col_stop = st.columns(4)
if col_start.button("Start From Pending", key="doe_exec_start"):
    plan_df = ensure_plan_columns(st.session_state.doe_exec_plan_df)
    st.session_state.doe_exec_plan_df = plan_df

    if plan_df.empty:
        st.error("Upload a DOE matrix first.")
    elif not st.session_state.get("doe_exec_param_cols"):
        st.error("Select at least one parameter column.")
    elif not st.session_state.get("doe_exec_objectives"):
        st.error("Select at least one objective to measure.")
    elif missing_required_cols:
        st.error("Add all required running-protocol parameter columns before starting.")
    else:
        pending_idx = plan_df.index[plan_df["__status"] == "pending"].tolist()
        if not pending_idx:
            st.info("No pending rows found.")
        else:
            st.session_state.doe_exec_runner = ExperimentRunner(
                OPCClient(st.session_state.opc_url),
                "doe_executor_log.csv",
                simulation_mode=st.session_state.simulation_mode,
                use_autosampler=st.session_state.use_autosampler,
                volume_to_collect=st.session_state.volume_to_collect,
                process_adapter=st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
                adapter_config=st.session_state.get("process_adapter_config", {}),
                running_protocol_script=st.session_state.get("running_protocol_script"),
            )
            st.session_state.doe_exec_queue = pending_idx
            st.session_state.doe_exec_running = True
            st.session_state.doe_exec_stop_requested = False
            st.session_state.doe_exec_db_saved = False

if col_resume.button("Resume All Open", key="doe_exec_resume_open"):
    plan_df = ensure_plan_columns(st.session_state.doe_exec_plan_df)
    st.session_state.doe_exec_plan_df = plan_df
    open_idx = plan_df.index[plan_df["__status"].isin(["pending", "failed"])].tolist()
    if plan_df.empty:
        st.error("Upload or load a DOE run first.")
    elif not open_idx:
        st.info("No pending or failed rows found.")
    elif missing_required_cols:
        st.error("Add all required running-protocol parameter columns before starting.")
    else:
        st.session_state.doe_exec_runner = ExperimentRunner(
            OPCClient(st.session_state.opc_url),
            "doe_executor_log.csv",
            simulation_mode=st.session_state.simulation_mode,
            use_autosampler=st.session_state.use_autosampler,
            volume_to_collect=st.session_state.volume_to_collect,
            process_adapter=st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            adapter_config=st.session_state.get("process_adapter_config", {}),
            running_protocol_script=st.session_state.get("running_protocol_script"),
        )
        st.session_state.doe_exec_queue = open_idx
        st.session_state.doe_exec_running = True
        st.session_state.doe_exec_stop_requested = False
        st.session_state.doe_exec_db_saved = False

if col_retry.button("Retry Failed", key="doe_exec_retry_failed"):
    plan_df = ensure_plan_columns(st.session_state.doe_exec_plan_df)
    st.session_state.doe_exec_plan_df = plan_df
    failed_idx = plan_df.index[plan_df["__status"] == "failed"].tolist()
    if plan_df.empty:
        st.error("Upload or load a DOE run first.")
    elif not failed_idx:
        st.info("No failed rows to retry.")
    elif missing_required_cols:
        st.error("Add all required running-protocol parameter columns before starting.")
    else:
        st.session_state.doe_exec_runner = ExperimentRunner(
            OPCClient(st.session_state.opc_url),
            "doe_executor_log.csv",
            simulation_mode=st.session_state.simulation_mode,
            use_autosampler=st.session_state.use_autosampler,
            volume_to_collect=st.session_state.volume_to_collect,
            process_adapter=st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            adapter_config=st.session_state.get("process_adapter_config", {}),
            running_protocol_script=st.session_state.get("running_protocol_script"),
        )
        st.session_state.doe_exec_queue = failed_idx
        st.session_state.doe_exec_running = True
        st.session_state.doe_exec_stop_requested = False
        st.session_state.doe_exec_db_saved = False

if col_stop.button("Stop", key="doe_exec_stop"):
    st.session_state.doe_exec_stop_requested = True
    st.session_state.doe_exec_running = False
    st.warning("Stop requested.")

# Execution loop
if st.session_state.get("doe_exec_running", False):
    if not st.session_state.get("doe_exec_objectives"):
        st.error("No objectives selected.")
        st.session_state.doe_exec_running = False
    elif st.session_state.get("doe_exec_runner") is None:
        st.error("Runner not initialized.")
        st.session_state.doe_exec_running = False
    else:
        log_placeholder = st.empty()
        logger = StreamlitLogger(placeholder=log_placeholder)
        sys.stdout = logger

        plan_df = ensure_plan_columns(st.session_state.doe_exec_plan_df)
        objectives = list(st.session_state.get("doe_exec_objectives", []))
        param_cols = list(st.session_state.get("doe_exec_param_cols", []))
        queue = list(st.session_state.get("doe_exec_queue", []))
        results = list(st.session_state.get("doe_exec_results", []))
        runner = st.session_state.get("doe_exec_runner")
        run_name = st.session_state.get("doe_exec_run_name", "doe_exec")
        run_path = os.path.join(SAVE_DIR, run_name)

        progress = st.progress(0.0)
        total_rows = len(plan_df) if len(plan_df) > 0 else 1

        while queue and st.session_state.get("doe_exec_running", False):
            if st.session_state.get("doe_exec_stop_requested", False):
                break

            row_idx = queue.pop(0)
            if row_idx not in plan_df.index:
                continue

            plan_df.at[row_idx, "__status"] = "running"
            plan_df.at[row_idx, "__error"] = ""

            params = {}
            conversion_error = None
            for col in param_cols:
                try:
                    value = plan_df.at[row_idx, col]
                    if pd.isna(value):
                        raise ValueError("missing value")
                    params[col] = float(value)
                except Exception:
                    conversion_error = f"Column '{col}' is not numeric at row {int(row_idx) + 1}."
                    break

            if conversion_error:
                plan_df.at[row_idx, "__status"] = "failed"
                plan_df.at[row_idx, "__error"] = conversion_error
            else:
                done_so_far = int((plan_df["__status"] == "done").sum())
                try:
                    result = runner.run_experiment(
                        params,
                        experiment_number=done_so_far + 1,
                        total_iterations=total_rows,
                        objectives=objectives,
                    )
                    row_payload = {
                        "Experiment #": len(results) + 1,
                        "Source Row": int(row_idx) + 1,
                        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    for col in plan_df.columns:
                        if col.startswith("__"):
                            continue
                        row_payload[col] = plan_df.at[row_idx, col]
                    for obj in objectives:
                        row_payload[obj] = result.get(obj)
                    row_payload["Status"] = "done"
                    results.append(row_payload)
                    plan_df.at[row_idx, "__status"] = "done"
                    plan_df.at[row_idx, "__error"] = ""
                except Exception as exc:
                    plan_df.at[row_idx, "__status"] = "failed"
                    plan_df.at[row_idx, "__error"] = str(exc)

            try:
                runner.save_full_measurements_to_csv(run_name)
            except Exception:
                pass

            metadata = make_metadata(
                run_name=run_name,
                notes=st.session_state.get("doe_exec_notes", ""),
                run_date=st.session_state.get("doe_exec_date"),
                param_cols=param_cols,
                objectives=objectives,
            )
            persist_run_state(run_path, plan_df, pd.DataFrame(results), metadata)

            st.session_state.doe_exec_plan_df = plan_df
            st.session_state.doe_exec_results = results
            st.session_state.doe_exec_queue = queue

            progress.progress(float((plan_df["__status"] == "done").sum()) / float(total_rows))
            time.sleep(0.2)

        st.session_state.doe_exec_running = False
        st.session_state.doe_exec_stop_requested = False

        final_counts = status_counts(plan_df)
        if final_counts["failed"] > 0:
            st.warning(
                f"Execution finished with failures. done: {final_counts['done']}, failed: {final_counts['failed']}."
            )
        else:
            st.success(f"Execution complete. done: {final_counts['done']}.")

# Results and exports
if st.session_state.get("doe_exec_results"):
    st.subheader("Execution Results")
    df_results = pd.DataFrame(st.session_state.doe_exec_results)
    st.dataframe(df_results, use_container_width=True)
    export_to_csv(df_results, filename=f"{st.session_state.doe_exec_run_name}_results.csv")
    export_to_excel(df_results, filename=f"{st.session_state.doe_exec_run_name}_results.xlsx")

    if st.button("Save Current Results to Experiment Database", key="doe_exec_save_db"):
        plan_df = ensure_plan_columns(st.session_state.get("doe_exec_plan_df", pd.DataFrame()))
        param_cols = list(st.session_state.get("doe_exec_param_cols", []))
        variables = build_variable_bounds(plan_df, param_cols)
        objectives = list(st.session_state.get("doe_exec_objectives", []))

        best_result = None
        if len(objectives) == 1 and objectives[0] in df_results.columns:
            try:
                best_result = df_results.loc[df_results[objectives[0]].idxmax()].to_dict()
            except Exception:
                best_result = None

        settings = {
            "method": "DOE Executor (External Matrix)",
            "objectives": objectives,
            "simulation_mode": st.session_state.get("simulation_mode", "off"),
            "opc_url": st.session_state.get("opc_url", "http://em-nun:57080"),
            "process_adapter": st.session_state.get("process_adapter", DEFAULT_PROCESS_ADAPTER),
            "running_protocol_script": st.session_state.get("running_protocol_script"),
            "source_file": st.session_state.get("doe_exec_loaded_file_name", ""),
        }
        db_handler.save_experiment(
            name=st.session_state.get("doe_exec_run_name", "doe_exec"),
            notes=st.session_state.get("doe_exec_notes", ""),
            variables=variables,
            df_results=df_results,
            best_result=best_result,
            settings=settings,
        )
        st.session_state.doe_exec_db_saved = True
        st.success("Saved to experiment database.")

