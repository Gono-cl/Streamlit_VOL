"""Helpers for storing and loading reusable campaign templates."""

from __future__ import annotations

import json
from .variables import normalize_variable
import os
from pathlib import Path


SCHEMA_VERSION = 2
DEFAULT_TEMPLATE_DIR = "campaign_templates"


def sanitize_template_name(name: str) -> str:
    if not isinstance(name, str):
        name = str(name)
    cleaned = name.strip().rstrip(".")
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        cleaned = cleaned.replace(ch, "_")
    cleaned = " ".join(cleaned.split())
    return cleaned or "campaign_template"


def _ensure_dir(base_dir: str) -> None:
    os.makedirs(base_dir, exist_ok=True)


def _template_path(template_name: str, base_dir: str = DEFAULT_TEMPLATE_DIR) -> Path:
    safe = sanitize_template_name(template_name)
    return Path(base_dir) / f"{safe}.json"


def normalize_variables(variables) -> list[dict]:
    out = []
    for raw in variables or []:
        try:
            name, low, high, unit, kind = normalize_variable(raw)
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        variable = {"name": name, "unit": unit, "type": kind}
        if kind == "categorical":
            variable["categories"] = low
        else:
            variable.update(lower=low, upper=high)
        out.append(variable)
    return out


def variables_as_tuples(variables) -> list[tuple]:
    return [normalize_variable(v) for v in normalize_variables(variables)]


def list_campaign_templates(mode: str | None = None, base_dir: str = DEFAULT_TEMPLATE_DIR) -> list[str]:
    _ensure_dir(base_dir)
    names = []
    for file_name in sorted(os.listdir(base_dir)):
        if not file_name.lower().endswith(".json"):
            continue
        template_name = Path(file_name).stem
        if mode is None:
            names.append(template_name)
            continue
        try:
            payload = load_campaign_template(template_name, base_dir=base_dir)
        except Exception:
            continue
        if payload and payload.get("mode") == mode:
            names.append(template_name)
    return names


def load_campaign_template(template_name: str, base_dir: str = DEFAULT_TEMPLATE_DIR):
    path = _template_path(template_name, base_dir=base_dir)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_campaign_template(template_name: str, template_data: dict, base_dir: str = DEFAULT_TEMPLATE_DIR) -> str:
    _ensure_dir(base_dir)
    safe_name = sanitize_template_name(template_name)
    payload = dict(template_data or {})
    payload["schema_version"] = int(payload.get("schema_version", SCHEMA_VERSION))
    payload["name"] = payload.get("name", safe_name)
    path = _template_path(safe_name, base_dir=base_dir)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return str(path)


def build_single_campaign_template(
    template_name: str,
    variables,
    optimization: dict,
    hardware: dict,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "single",
        "name": sanitize_template_name(template_name),
        "variables": normalize_variables(variables),
        "optimization": dict(optimization or {}),
        "hardware": dict(hardware or {}),
    }


def build_multi_campaign_template(
    template_name: str,
    variables,
    optimization: dict,
    hardware: dict,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "multi",
        "name": sanitize_template_name(template_name),
        "variables": normalize_variables(variables),
        "optimization": dict(optimization or {}),
        "hardware": dict(hardware or {}),
    }
