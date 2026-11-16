"""Variable definitions and domains shared by campaign pages and optimizers.

Continuous domains are (lower, upper) tuples; categorical domains are lists.
The distinction is explicit even when a category list contains two numbers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping


def parse_categories(values):
    if isinstance(values, str):
        values = values.split(",")
    levels = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not levels:
        raise ValueError("Enter at least one category, separated by commas.")
    return levels


def normalize_variable(variable):
    """Read legacy four-field variables and current five-field definitions."""
    if isinstance(variable, Mapping):
        name = variable.get("name", "")
        kind = str(variable.get("type", "continuous")).lower()
        low = variable.get("categories") if kind == "categorical" else variable.get("lower")
        high, unit = variable.get("upper"), variable.get("unit", "")
    else:
        name, low, high = variable[:3]
        unit = variable[3] if len(variable) > 3 else ""
        kind = str(variable[4]).lower() if len(variable) > 4 else "continuous"
    name, unit = str(name).strip(), str(unit or "")
    if not name:
        raise ValueError("Enter a variable name.")
    if kind == "categorical":
        return (name, parse_categories(low), None, unit, kind)
    if kind != "continuous":
        raise ValueError(f"Unknown variable type '{kind}'.")
    low, high = float(low), float(high)
    if not (math.isfinite(low) and math.isfinite(high) and low < high):
        raise ValueError("Continuous bounds must be finite and lower < upper.")
    return (name, low, high, unit, kind)


def variable_domains(variables):
    return [low if kind == "categorical" else (low, high)
            for _, low, high, _, kind in map(normalize_variable, variables)]


def is_categorical(domain):
    return isinstance(domain, list)


def coerce_value(value, domain):
    if is_categorical(domain):
        for level in domain:
            if str(value).strip() == str(level).strip():
                return level
        # CSV readers may load numeric-looking labels as numbers.
        for level in domain:
            try:
                if float(value) == float(level):
                    return level
            except (TypeError, ValueError):
                continue
        raise ValueError(f"Unknown category {value!r}; allowed values: {domain}.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Continuous values must be finite.")
    return number


def point_from_row(row, variables, allow_new_categories=False):
    point = []
    for variable, domain in zip(variables, variable_domains(variables)):
        value = row[variable[0]]
        if allow_new_categories and is_categorical(domain):
            try:
                point.append(coerce_value(value, domain))
                continue
            except (TypeError, ValueError):
                pass
            label = str(value).strip()
            if not label or label.lower() in {"nan", "none"}:
                raise ValueError(f"Missing category for '{variable[0]}'.")
            point.append(label)
        else:
            point.append(coerce_value(value, domain))
    return point


def parameters_from_row(row, variables, allow_new_categories=False):
    return dict(zip((v[0] for v in variables),
                    point_from_row(row, variables, allow_new_categories)))


def read_campaign_data(path, variables):
    """Keep CSV category labels such as '01' and 'NA' unchanged."""
    import pandas as pd

    converters = {v[0]: str for v in map(normalize_variable, variables) if v[4] == "categorical"}
    return pd.read_csv(path, converters=converters)


def clip_point(point, domains):
    if len(point) != len(domains):
        raise ValueError("Point does not match the number of variables.")
    result = []
    for value, domain in zip(point, domains):
        if is_categorical(domain):
            result.append(value if value in domain else domain[0])
        else:
            result.append(min(max(coerce_value(value, domain), domain[0]), domain[1]))
    return result


def reference_point(variables, categories=None):
    """Numeric midpoints with a selected, fixed categorical reference level."""
    categories = categories or {}
    return {v[0]: coerce_value(categories.get(v[0], domain[0]), domain)
            if is_categorical(domain) else (domain[0] + domain[1]) / 2.0
            for v, domain in zip(variables, variable_domains(variables))}


def expanded_domains(variables, dataframe=None):
    """Include selected reused observations in the model's domain."""
    domains = variable_domains(variables)
    if dataframe is None or dataframe.empty:
        return domains
    for _, row in dataframe.iterrows():
        if not bool(row.get("Select", True)):
            continue
        try:
            values = point_from_row(row, variables, allow_new_categories=True)
        except (KeyError, TypeError, ValueError):
            continue
        for index, value in enumerate(values):
            domain = domains[index]
            if is_categorical(domain):
                if value not in domain:
                    domain.append(value)
            else:
                domains[index] = (min(domain[0], value), max(domain[1], value))
    return domains


def optimizer_dimensions(variables, domains=None, backend="skopt"):
    if backend == "ProcessOptimizer":
        from ProcessOptimizer.space import Real, Categorical
    else:
        from skopt.space import Real, Categorical
    domains = variable_domains(variables) if domains is None else domains
    return [Categorical(domain, name=v[0]) if is_categorical(domain)
            else Real(*domain, name=v[0]) for v, domain in zip(variables, domains)]


def variable_axis(variable, **kwargs):
    import altair as alt

    name, low, high, _, kind = normalize_variable(variable)
    if kind == "categorical":
        return alt.X(f"{name}:N", sort=low, **kwargs)
    return alt.X(f"{name}:Q", scale=alt.Scale(domain=[low, high], nice=False), **kwargs)
