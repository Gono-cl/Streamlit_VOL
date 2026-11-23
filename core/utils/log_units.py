"""Unit labels at the file boundary; internal optimization keys stay stable."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path


MB_VARIABLE_UNITS = {
    "current_density": "mA/cm2",
    "applied_charge": "F/mol",
    "substrate_concentration": "mg/L",
    "electrolyte_concentration": "mol/L",
}
ABSORBANCE_COLUMNS = {
    "Absorbance", "Absorbance SD", "UV/VIS Absorbance", "UV/VIS Absorbance SD",
}
CONCENTRATION_COLUMNS = {
    "Value", "Measured Concentration", "Corrected Concentration", "Inlet Concentration",
    "Outlet Concentration", "UV/VIS Concentration", "UV/VIS Measured Concentration",
    "UV/VIS Corrected Concentration", "Concentration",
}
FIXED_UNITS = {"Yield": "%", "Degradation": "%", "Used Organic": "mL"}


def variable_units(variables=(), protocol_script=None):
    """Read campaign units; the fixed MB protocol defines its own input units."""
    units = {}
    for definition in variables or ():
        if isinstance(definition, dict):
            name, unit = definition.get("name", ""), definition.get("unit", "")
            kind = definition.get("type", "continuous")
        else:
            name = definition[0]
            unit = definition[3] if len(definition) > 3 else ""
            kind = definition[4] if len(definition) > 4 else "continuous"
        if kind == "categorical":
            continue
        if str(unit or "").strip():
            units[str(name)] = str(unit).strip()
    if protocol_script and Path(str(protocol_script)).stem == "MB_6_variables":
        units.update(MB_VARIABLE_UNITS)
    return units


def unit_header(name, unit):
    name = str(name)
    unit = unit.strip() if isinstance(unit, str) else ""
    # Repeated saves must not add a second suffix to an already labelled column.
    return f"{name} ({unit})" if unit and not name.endswith(f" ({unit})") else name


def unit_labeled_row(row, *, units=None, objective=None, measurement_unit=None):
    """Label each row from its own metadata, including mixed-unit old logs.

    Missing historical concentration units are intentionally not guessed from
    today's calibration. Columns with existing suffixes are kept as written.
    """
    units = units or {}
    concentration_unit = row.get("Concentration Unit")
    if not isinstance(concentration_unit, str) or not concentration_unit.strip():
        concentration_unit = ""
    else:
        concentration_unit = concentration_unit.strip()
    result = {}
    for name, value in row.items():
        unit = units.get(name, FIXED_UNITS.get(name, ""))
        if name in ABSORBANCE_COLUMNS:
            unit = "AU"
        elif name in CONCENTRATION_COLUMNS:
            unit = concentration_unit or unit
        elif name == "Stability Limit":
            unit = row.get("Stability Units", "")
        elif name == "UV/VIS Stability Limit":
            unit = row.get("UV/VIS Stability Units", "")
        elif name == "Measurement":
            unit = FIXED_UNITS.get(objective, concentration_unit if objective == "Concentration" else "")
        if name == "Value" and not unit and measurement_unit:
            unit = measurement_unit
        if name == "Timestamp" and isinstance(value, str):
            try:
                offset = datetime.fromisoformat(value).utcoffset()
                if offset is not None and offset.total_seconds() == 0:
                    unit = "UTC"
            except ValueError:
                pass
        title = unit_header(name, unit)
        if title != name and title in row:
            if _blank(value):
                # A previously upgraded log can retain an unknown-unit legacy
                # column. Its empty cells must not collide with labelled data.
                result[name] = value
                continue
            if not _blank(row[title]):
                raise ValueError(f"Unit labels would create duplicate column '{title}'; data not written.")
        if title in result:
            if _blank(value):
                continue
            if not _blank(result[title]):
                raise ValueError(f"Unit labels would create duplicate column '{title}'; data not written.")
        result[title] = value
    return result


def _blank(value):
    return value is None or (isinstance(value, str) and value == "") or (isinstance(value, float) and math.isnan(value))


def unit_labeled_dataframe(dataframe, *, variables=(), protocol_script=None, objective=None):
    """Return an export copy, preserving strings, data and the original frame."""
    import pandas as pd

    if not dataframe.columns.is_unique:
        raise ValueError("Cannot label units for a table with duplicate column names.")
    units = variable_units(variables, protocol_script)
    if dataframe.empty:
        labels = unit_labeled_row(dict.fromkeys(dataframe.columns), units=units, objective=objective)
        return dataframe.rename(columns=dict(zip(dataframe.columns, labels)))
    # A single CSV may legitimately contain different calibration units or both
    # CV and SD decisions. Separate columns keep every number's unit explicit.
    rows = [unit_labeled_row(row, units=units, objective=objective)
            for row in dataframe.to_dict(orient="records")]
    return pd.DataFrame(rows, index=dataframe.index)
