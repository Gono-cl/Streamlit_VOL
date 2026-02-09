import pandas as pd
import streamlit as st

from core.hardware.process_adapters import (
    DEFAULT_ECHEM_PUMP_TAGS,
    DEFAULT_PROCESS_ADAPTER,
    PROCESS_ADAPTER_LABELS,
    available_process_adapters,
    create_process_adapter,
)
from core.hardware.process_profiles import (
    delete_process_profile,
    list_process_profiles,
    load_process_profile,
    save_process_profile,
)


def default_adapter_config(adapter_name: str) -> dict:
    if adapter_name == "echem_3pump":
        return {
            "flow_rate_key": "flow_rate",
            "voltage_key": "Voltage",
            "residence_time_key": "residence_time",
            "substrate_key": "substrate_concentration",
            "filling_volume_ml": 0.8,
            "solvent_pump_tag": DEFAULT_ECHEM_PUMP_TAGS["solvent"],
            "components": [
                {
                    "name": "substrate",
                    "param": "substrate_concentration",
                    "stock_concentration": 716.0,
                    "pump_tag": DEFAULT_ECHEM_PUMP_TAGS["substrate"],
                },
                {
                    "name": "acid",
                    "param": "acid_concentration",
                    "stock_concentration": 1500.0,
                    "pump_tag": DEFAULT_ECHEM_PUMP_TAGS["acid"],
                },
                {
                    "name": "base",
                    "param": "base_concentration",
                    "stock_concentration": 1500.0,
                    "pump_tag": DEFAULT_ECHEM_PUMP_TAGS["base"],
                },
            ],
        }

    return {
        "flow_rate_key": "flow_rate",
        "voltage_key": "Voltage",
        "residence_time_key": "residence_time",
        "substrate_key": "substrate_concentration",
        "acid_key": "acid_concentration",
        "base_key": "base_concentration",
        "filling_volume_ml": 0.8,
    }


def ensure_builder_state():
    if "builder_profile_name" not in st.session_state:
        st.session_state.builder_profile_name = "new_process_profile"
    if "builder_adapter" not in st.session_state:
        st.session_state.builder_adapter = DEFAULT_PROCESS_ADAPTER
    if "builder_adapter_config" not in st.session_state:
        st.session_state.builder_adapter_config = default_adapter_config(DEFAULT_PROCESS_ADAPTER)
    if "builder_preview_df" not in st.session_state:
        st.session_state.builder_preview_df = pd.DataFrame()
    if "builder_preview_source" not in st.session_state:
        st.session_state.builder_preview_source = ""


def ensure_preview_editor_state(required_keys: list[str]):
    source_id = "|".join(required_keys)
    if st.session_state.get("builder_preview_source") != source_id:
        seed = st.session_state.get("builder_preview_inputs", {})
        rows = []
        for k in required_keys:
            rows.append({"parameter": k, "value": float(seed.get(k, default_preview_value(k)))})
        st.session_state.builder_preview_df = pd.DataFrame(rows)
        st.session_state.builder_preview_source = source_id


def load_profile_into_builder(profile_name: str):
    payload = load_process_profile(profile_name)
    if not payload:
        return False
    adapter = payload.get("adapter", DEFAULT_PROCESS_ADAPTER)
    config = payload.get("adapter_config", {})
    st.session_state.builder_profile_name = payload.get("name", profile_name)
    st.session_state.builder_adapter = adapter
    st.session_state.builder_adapter_config = config if isinstance(config, dict) else {}
    return True


def default_preview_value(param_key: str) -> float:
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


def code_preview_for_adapter(adapter_name: str, config: dict) -> str:
    if adapter_name == "echem_3pump":
        components = config.get("components", [])
        lines = [
            "def prepare_hardware(runner, parameters):",
            f"    flow_rate = parameters['{config.get('flow_rate_key', 'flow_rate')}']",
            f"    voltage = parameters['{config.get('voltage_key', 'Voltage')}']",
            "    # 1) Set reaction valves",
            "    # 2) Compute each reagent flow = target_conc * flow_rate / stock_concentration",
            "    # 3) Solvent flow = flow_rate - sum(reagent_flows)",
        ]
        for comp in components:
            name = comp.get("name", "component")
            param = comp.get("param", "")
            stock = comp.get("stock_concentration", "")
            tag = comp.get("pump_tag", "")
            lines.append(f"    # {name}: param='{param}', stock={stock}, pump_tag='{tag}'")
        lines += [
            "    # 4) Wait filling time (filling_volume / flow_rate * 1.5 * 60)",
            "    # 5) Set voltage, turn on power supply, countdown",
        ]
        return "\n".join(lines)

    return "\n".join(
        [
            "def prepare_hardware(runner, parameters):",
            f"    flow_rate = parameters['{config.get('flow_rate_key', 'flow_rate')}']",
            f"    substrate_conc = parameters['{config.get('substrate_key', 'substrate_concentration')}']",
            f"    acid_conc = parameters['{config.get('acid_key', 'acid_concentration')}']",
            f"    base_conc = parameters['{config.get('base_key', 'base_concentration')}']",
            f"    voltage = parameters['{config.get('voltage_key', 'Voltage')}']",
            "    # 1) flow_electrochemical_cell_from_four_variables(flow_rate, substrate_conc, acid_conc, base_conc)",
            "    # 2) Wait filling time (filling_volume / flow_rate * 1.5 * 60)",
            "    # 3) Set voltage, turn on power supply, countdown",
        ]
    )


st.title("Process Builder")
st.caption("Create and manage reusable process profiles for optimization campaigns.")
ensure_builder_state()

profiles = list_process_profiles()
selected_existing = st.selectbox("Existing profiles", options=["None"] + profiles, key="builder_existing_profile")
col_load, col_delete = st.columns([1, 1])
with col_load:
    if st.button("Load Existing Profile"):
        if selected_existing == "None":
            st.warning("Select a profile first.")
        elif load_profile_into_builder(selected_existing):
            st.success(f"Loaded profile: {selected_existing}")
            st.rerun()
        else:
            st.error("Failed to load profile.")
with col_delete:
    if st.button("Delete Selected Profile", disabled=(selected_existing == "None")):
        if delete_process_profile(selected_existing):
            st.success(f"Deleted profile: {selected_existing}")
            st.rerun()
        else:
            st.error("Could not delete selected profile.")

st.markdown("---")
st.subheader("Profile Editor")
profile_name = st.text_input("Profile name", value=st.session_state.get("builder_profile_name", "new_process_profile"))
adapter_options = available_process_adapters()
adapter_default = st.session_state.get("builder_adapter", DEFAULT_PROCESS_ADAPTER)
if adapter_default not in adapter_options:
    adapter_default = adapter_options[0]
adapter_name = st.selectbox(
    "Process adapter",
    options=adapter_options,
    index=adapter_options.index(adapter_default),
    format_func=lambda x: PROCESS_ADAPTER_LABELS.get(x, x),
)

if adapter_name != st.session_state.get("builder_adapter"):
    st.session_state.builder_adapter = adapter_name
    st.session_state.builder_adapter_config = default_adapter_config(adapter_name)
    st.session_state.builder_preview_source = ""

cfg = dict(st.session_state.get("builder_adapter_config", {}))
defaults = default_adapter_config(adapter_name)
for k, v in defaults.items():
    cfg.setdefault(k, v)

adapter_config = dict(cfg)

st.session_state.builder_profile_name = profile_name
st.session_state.builder_adapter = adapter_name
st.session_state.builder_adapter_config = adapter_config

st.markdown("### Runtime Behavior Preview")
adapter_instance = create_process_adapter(adapter_name, config=adapter_config)
required_keys = adapter_instance.required_parameter_keys()
st.write("Required parameter keys for `prepare_hardware`:")
st.code("\n".join(required_keys) if required_keys else "None", language="text")

if "builder_preview_inputs" not in st.session_state:
    st.session_state.builder_preview_inputs = {}
ensure_preview_editor_state(required_keys)
with st.form("builder_preview_form", clear_on_submit=False):
    edited_preview_df = st.data_editor(
        st.session_state.builder_preview_df,
        num_rows="fixed",
        use_container_width=True,
        key="builder_preview_inputs_editor",
        column_config={
            "value": st.column_config.NumberColumn("value", format="%.6f"),
        },
    )
    apply_preview = st.form_submit_button("Apply Preview Inputs")
if apply_preview:
    st.session_state.builder_preview_df = edited_preview_df.copy()
    updated_preview_inputs = {}
    for _, row in edited_preview_df.iterrows():
        pname = str(row.get("parameter", "")).strip()
        if not pname:
            continue
        try:
            updated_preview_inputs[pname] = float(row.get("value", 0.0))
        except Exception:
            updated_preview_inputs[pname] = default_preview_value(pname)
    st.session_state.builder_preview_inputs = updated_preview_inputs
    st.rerun()

preview_seed = st.session_state.get("builder_preview_inputs", {})
preview_params = {}
for key in required_keys:
    try:
        preview_params[key] = float(preview_seed.get(key, default_preview_value(key)))
    except Exception:
        preview_params[key] = default_preview_value(key)
st.session_state.builder_preview_inputs = preview_params

try:
    runtime_preview = adapter_instance.preview_prepare(preview_params)
    if runtime_preview.get("warnings"):
        for msg in runtime_preview.get("warnings", []):
            st.warning(msg)
    st.markdown("#### Ordered Actions")
    for i, action in enumerate(runtime_preview.get("actions", []), 1):
        st.write(f"{i}. {action}")
    st.markdown("#### Computed Values")
    st.json(runtime_preview.get("computed", {}))
except Exception as exc:
    st.error(f"Cannot build runtime preview yet: {exc}")

st.markdown("#### Python-Style `prepare_hardware` Preview")
st.code(code_preview_for_adapter(adapter_name, adapter_config), language="python")

col_save, col_apply = st.columns([1, 1])
with col_save:
    if st.button("Save Profile"):
        path = save_process_profile(profile_name, adapter_name, adapter_config)
        st.success(f"Saved profile: {path}")
with col_apply:
    if st.button("Apply To Current Session"):
        st.session_state.process_adapter = adapter_name
        st.session_state.process_adapter_config = adapter_config
        st.success("Profile applied to current session.")

st.markdown("### Use In Campaign")
col_to_single, col_to_multi = st.columns([1, 1])
with col_to_single:
    if st.button("Use In Single Objective"):
        st.session_state.process_adapter = adapter_name
        st.session_state.process_adapter_config = adapter_config
        st.session_state.selected_page = "🎯 Autonomous Single Objective Optimization"
        st.rerun()
with col_to_multi:
    if st.button("Use In Multi-Objective"):
        st.session_state.process_adapter = adapter_name
        st.session_state.process_adapter_config = adapter_config
        st.session_state.selected_page = "📊 Autonomous Multi-Objective Optimization"
        st.rerun()
