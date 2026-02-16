import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.hardware.protocol_scripts import (
    PROTOCOL_SCRIPTS_DIR,
    list_protocol_scripts,
    protocol_info,
    protocol_required_parameter_keys,
)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


def _normalize_images(raw_images):
    if isinstance(raw_images, (str, Path)):
        return [raw_images]
    if isinstance(raw_images, list):
        return raw_images
    return []


def _resolve_image_entries(raw_images):
    base_dir = Path(PROTOCOL_SCRIPTS_DIR).resolve()
    entries = []
    for idx, item in enumerate(_normalize_images(raw_images), 1):
        if isinstance(item, dict):
            rel_path = str(item.get("path", "")).strip()
            caption = str(item.get("caption", "")).strip() or f"Image {idx}"
        else:
            rel_path = str(item).strip()
            caption = f"Image {idx}"
        if not rel_path:
            continue

        abs_path = (base_dir / rel_path).resolve()
        is_inside = True
        try:
            abs_path.relative_to(base_dir)
        except Exception:
            is_inside = False
        exists = bool(is_inside and abs_path.exists() and abs_path.is_file())

        entries.append(
            {
                "path": rel_path.replace("\\", "/"),
                "caption": caption,
                "exists": exists,
                "abs_path": abs_path,
            }
        )
    return entries


def _build_summary_text(selected_script, info, required_keys, image_entries):
    title = str(info.get("title", "")).strip() or selected_script
    description = str(info.get("description", "")).strip() or "No description"
    measurement_prefix = str(info.get("measurement_source_prefix", "")).strip()
    measurement_signal = str(info.get("measurement_source_signal", "")).strip()
    measurement_tag = str(info.get("measurement_source_tag", "")).strip()

    lines = [
        "Running Protocol Summary",
        "========================",
        f"Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"Protocol file: {selected_script}",
        f"Title: {title}",
        "",
        "Description:",
        description,
        "",
        "Required parameter keys:",
    ]
    if required_keys:
        for key in required_keys:
            lines.append(f"- {key}")
    else:
        lines.append("- None")

    lines.append("")
    lines.append("Measurement source:")
    if measurement_tag:
        lines.append(f"- measurement_source_tag: {measurement_tag}")
    else:
        lines.append(f"- measurement_source_prefix: {measurement_prefix or '(not set)'}")
        lines.append(f"- measurement_source_signal: {measurement_signal or '(not set)'}")

    lines.append("")
    lines.append("Images/assets:")
    if image_entries:
        for entry in image_entries:
            state = "OK" if entry["exists"] else "MISSING"
            lines.append(f"- {entry['path']} [{state}]")
    else:
        lines.append("- None")

    return "\n".join(lines)


def _build_bundle_bytes(selected_script, source, info, required_keys, image_entries):
    summary_text = _build_summary_text(selected_script, info, required_keys, image_entries)
    manifest = {
        "protocol_file": selected_script,
        "exported_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": str(info.get("title", "")).strip(),
        "description": str(info.get("description", "")).strip(),
        "required_parameter_keys": list(required_keys),
        "measurement_source_prefix": str(info.get("measurement_source_prefix", "")).strip(),
        "measurement_source_signal": str(info.get("measurement_source_signal", "")).strip(),
        "measurement_source_tag": str(info.get("measurement_source_tag", "")).strip(),
        "images": [
            {
                "path": entry["path"],
                "caption": entry["caption"],
                "exists": bool(entry["exists"]),
            }
            for entry in image_entries
        ],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(selected_script, source)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("README.txt", summary_text)
        for entry in image_entries:
            if not entry["exists"]:
                continue
            zf.write(str(entry["abs_path"]), arcname=entry["path"])
    return buffer.getvalue()


def _build_summary_pdf(summary_text, selected_script, source, image_entries):
    buf = io.BytesIO()
    doc = canvas.Canvas(buf, pagesize=A4)
    page_width, page_height = A4
    x = 40
    y = page_height - 40
    line_height = 13
    max_width = page_width - (2 * x)

    def ensure_space(min_y=50):
        nonlocal y
        if y < min_y:
            doc.showPage()
            y = page_height - 40

    def draw_wrapped_text(text, font_name="Helvetica", font_size=10, lead=13):
        nonlocal y
        doc.setFont(font_name, font_size)
        for raw_line in text.splitlines():
            line = raw_line if raw_line else " "
            while line:
                chunk = line
                while doc.stringWidth(chunk, font_name, font_size) > max_width and len(chunk) > 1:
                    chunk = chunk[:-1]
                if not chunk:
                    chunk = line[:1]
                doc.drawString(x, y, chunk)
                y -= lead
                ensure_space()
                line = line[len(chunk):]
            if raw_line == "":
                y -= lead
                ensure_space()

    # Section 1: summary
    doc.setFont("Helvetica-Bold", 13)
    doc.drawString(x, y, f"Protocol Summary: {selected_script}")
    y -= 20
    draw_wrapped_text(summary_text, font_name="Helvetica", font_size=10, lead=line_height)

    # Section 2: images
    ensure_space(120)
    y -= 8
    doc.setFont("Helvetica-Bold", 12)
    doc.drawString(x, y, "Protocol Images")
    y -= 18
    if not image_entries:
        draw_wrapped_text("No images declared in protocol_info().", font_name="Helvetica", font_size=10, lead=line_height)
    else:
        for idx, entry in enumerate(image_entries, 1):
            ensure_space(140)
            doc.setFont("Helvetica", 10)
            doc.drawString(x, y, f"{idx}. {entry['caption']} ({entry['path']})")
            y -= 14
            if not entry["exists"]:
                draw_wrapped_text("Image file not found.", font_name="Helvetica-Oblique", font_size=9, lead=11)
                continue

            ext = entry["abs_path"].suffix.lower()
            drawn = False
            target_w = max_width
            target_h = 220
            if ext in [".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]:
                try:
                    doc.drawImage(
                        str(entry["abs_path"]),
                        x,
                        y - target_h,
                        width=target_w,
                        height=target_h,
                        preserveAspectRatio=True,
                        anchor="nw",
                    )
                    y -= target_h + 8
                    drawn = True
                except Exception:
                    drawn = False
            elif ext == ".svg":
                try:
                    from svglib.svglib import svg2rlg
                    from reportlab.graphics import renderPDF

                    drawing = svg2rlg(str(entry["abs_path"]))
                    if drawing and getattr(drawing, "width", 0) and getattr(drawing, "height", 0):
                        scale = min(target_w / float(drawing.width), target_h / float(drawing.height))
                        drawing.scale(scale, scale)
                        renderPDF.draw(drawing, doc, x, y - (float(drawing.height) * scale))
                        y -= (float(drawing.height) * scale) + 8
                        drawn = True
                except Exception:
                    drawn = False

            if not drawn:
                draw_wrapped_text(
                    "Could not render this image in PDF (unsupported format or missing backend).",
                    font_name="Helvetica-Oblique",
                    font_size=9,
                    lead=11,
                )

    # Section 3: source code
    doc.showPage()
    y = page_height - 40
    doc.setFont("Helvetica-Bold", 12)
    doc.drawString(x, y, "Protocol Source Code")
    y -= 18

    doc.setFont("Courier", 8)
    max_code_chars = 120
    for raw_line in source.splitlines():
        line = raw_line if raw_line else " "
        while len(line) > max_code_chars:
            ensure_space()
            doc.drawString(x, y, line[:max_code_chars])
            line = line[max_code_chars:]
            y -= 10
        ensure_space()
        doc.drawString(x, y, line)
        y -= 10

    doc.save()
    return buf.getvalue()


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

file_path = Path(PROTOCOL_SCRIPTS_DIR) / selected_script
source = ""
source_error = None
try:
    source = file_path.read_text(encoding="utf-8")
except Exception as exc:
    source_error = str(exc)

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

image_entries = _resolve_image_entries(info.get("images", []))
summary_text = _build_summary_text(selected_script, info, required_keys, image_entries)

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

    if image_entries:
        st.markdown("**Protocol Scheme / Images**")
        for entry in image_entries:
            if entry["exists"]:
                st.image(str(entry["abs_path"]), caption=entry["caption"], use_container_width=True)
            else:
                st.warning(f"Image path not found: {entry['path']}")

st.markdown("---")
st.subheader("Export")
col_py, col_bundle, col_doc = st.columns(3)

if source_error:
    st.error(f"Could not read `{file_path}`: {source_error}")
else:
    col_py.download_button(
        label="Download .py",
        data=source.encode("utf-8"),
        file_name=selected_script,
        mime="text/x-python",
        key="download_protocol_py",
    )

    bundle_bytes = _build_bundle_bytes(selected_script, source, info, required_keys, image_entries)
    col_bundle.download_button(
        label="Download Bundle (.zip)",
        data=bundle_bytes,
        file_name=f"{Path(selected_script).stem}_bundle.zip",
        mime="application/zip",
        key="download_protocol_bundle",
    )

    if REPORTLAB_AVAILABLE:
        pdf_bytes = _build_summary_pdf(summary_text, selected_script, source, image_entries)
        col_doc.download_button(
            label="Download Summary (.pdf)",
            data=pdf_bytes,
            file_name=f"{Path(selected_script).stem}_summary.pdf",
            mime="application/pdf",
            key="download_protocol_pdf",
        )
    else:
        col_doc.download_button(
            label="Download Summary (.txt)",
            data=summary_text.encode("utf-8"),
            file_name=f"{Path(selected_script).stem}_summary.txt",
            mime="text/plain",
            key="download_protocol_txt",
        )
        st.caption("Install `reportlab` to enable PDF summary export.")

st.markdown("---")
st.subheader("Protocol Source")
if source_error:
    st.error(f"Could not read `{file_path}`: {source_error}")
else:
    st.code(source, language="python")
