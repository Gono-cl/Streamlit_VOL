import os
from pathlib import Path

import streamlit as st

from core.hardware.protocol_scripts import (
    PROTOCOL_SCRIPTS_DIR,
    list_protocol_scripts,
    protocol_info,
    protocol_required_parameter_keys,
)


st.title("Running Protocol Library")
st.caption("Browse protocol files, inspect their content, and set one as active for this session.")

os.makedirs(PROTOCOL_SCRIPTS_DIR, exist_ok=True)
scripts = list_protocol_scripts()

st.markdown(f"Protocol folder: `{PROTOCOL_SCRIPTS_DIR}`")
st.markdown(f"Detected protocol files: **{len(scripts)}**")

if not scripts:
    st.info(
        "No protocol files found. Add `.py` files under `running_protocols/` "
        "and they will appear here automatically."
    )
    st.stop()

default_script = st.session_state.get("running_protocol_script")
if default_script not in scripts:
    default_script = scripts[0]

selected_script = st.selectbox(
    "Select protocol file",
    options=scripts,
    index=scripts.index(default_script),
    key="running_protocol_library_select",
)

col_set, col_clear = st.columns(2)
with col_set:
    if st.button("Set As Active Protocol", key="set_active_protocol"):
        st.session_state.running_protocol_script = selected_script
        st.success(f"Active protocol set to: {selected_script}")
with col_clear:
    if st.button("Clear Active Protocol", key="clear_active_protocol"):
        st.session_state.running_protocol_script = None
        st.success("Active protocol cleared.")

active = st.session_state.get("running_protocol_script")
st.markdown(f"Current active protocol: `{active if active else 'None'}`")

st.markdown("---")
st.subheader("Protocol Summary")

required_keys = []
info = {}
error_text = None
try:
    required_keys = protocol_required_parameter_keys(selected_script)
    info = protocol_info(selected_script)
except Exception as exc:
    error_text = str(exc)

st.markdown(f"Selected file: `{selected_script}`")
if error_text:
    st.error(f"Error while reading protocol hooks: {error_text}")
else:
    title = str(info.get("title", "")).strip()
    description = str(info.get("description", "")).strip()
    if title:
        st.markdown(f"**Title:** {title}")
    if description:
        st.write(description)

    if required_keys:
        st.markdown("`required_parameter_keys()`")
        st.code(", ".join(required_keys), language="text")
    else:
        st.info("No `required_parameter_keys()` found or it returned no keys.")

    raw_images = info.get("images", [])
    if isinstance(raw_images, (str, Path)):
        raw_images = [raw_images]
    if raw_images:
        st.markdown("**Protocol Scheme / Images**")
        for idx, item in enumerate(raw_images, 1):
            if isinstance(item, dict):
                rel_path = str(item.get("path", "")).strip()
                caption = str(item.get("caption", "")).strip() or f"Image {idx}"
            else:
                rel_path = str(item).strip()
                caption = f"Image {idx}"
            if not rel_path:
                continue
            img_path = (Path(PROTOCOL_SCRIPTS_DIR) / rel_path).resolve()
            if img_path.exists():
                st.image(str(img_path), caption=caption, use_container_width=True)
            else:
                st.warning(f"Image path not found: {rel_path}")

st.markdown("---")
st.subheader("Protocol Source")
file_path = Path(PROTOCOL_SCRIPTS_DIR) / selected_script
try:
    source = file_path.read_text(encoding="utf-8")
    st.code(source, language="python")
except Exception as exc:
    st.error(f"Could not read `{file_path}`: {exc}")
