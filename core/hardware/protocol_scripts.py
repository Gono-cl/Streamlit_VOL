"""Utilities for loading executable running protocol scripts from disk."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


PROTOCOL_SCRIPTS_DIR = "running_protocols"


def _ensure_protocol_dir(base_dir: str = PROTOCOL_SCRIPTS_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)


def _normalize_script_name(script_name: str) -> str:
    if not isinstance(script_name, str):
        script_name = str(script_name)
    clean = os.path.basename(script_name.strip())
    if not clean:
        return ""
    if not clean.lower().endswith(".py"):
        clean = f"{clean}.py"
    return clean


def list_protocol_scripts(base_dir: str = PROTOCOL_SCRIPTS_DIR) -> list[str]:
    _ensure_protocol_dir(base_dir)
    items: list[str] = []
    for file_name in sorted(os.listdir(base_dir)):
        if not file_name.lower().endswith(".py"):
            continue
        if file_name.startswith("_"):
            continue
        items.append(file_name)
    return items


def protocol_script_path(script_name: str, base_dir: str = PROTOCOL_SCRIPTS_DIR) -> Path:
    clean = _normalize_script_name(script_name)
    if not clean:
        return Path(base_dir) / "__invalid__.py"
    return Path(base_dir) / clean


def load_protocol_module(script_name: str, base_dir: str = PROTOCOL_SCRIPTS_DIR):
    clean = _normalize_script_name(script_name)
    if not clean:
        return None
    path = protocol_script_path(clean, base_dir=base_dir)
    if not path.exists():
        return None

    module_key = f"protocol_{path.stem}_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load protocol script from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protocol_required_parameter_keys(script_name: str, base_dir: str = PROTOCOL_SCRIPTS_DIR) -> list[str]:
    module = load_protocol_module(script_name, base_dir=base_dir)
    if module is None:
        return []
    fn = getattr(module, "required_parameter_keys", None)
    if not callable(fn):
        return []
    keys = fn()
    if not keys:
        return []
    out = []
    for item in keys:
        name = str(item).strip()
        if name:
            out.append(name)
    return out


def protocol_info(script_name: str, base_dir: str = PROTOCOL_SCRIPTS_DIR) -> dict:
    """
    Return optional metadata from protocol script.

    Expected hook in protocol file:
        def protocol_info() -> dict:
            return {
                "title": "...",
                "description": "...",
                "images": ["assets/my_scheme.svg"],
            }
    """
    module = load_protocol_module(script_name, base_dir=base_dir)
    if module is None:
        return {}
    fn = getattr(module, "protocol_info", None)
    if not callable(fn):
        return {}
    info = fn()
    if not isinstance(info, dict):
        return {}
    return info
