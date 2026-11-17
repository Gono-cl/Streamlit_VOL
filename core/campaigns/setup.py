"""Build and restore campaign configuration without requiring experiment data."""

from datetime import datetime

from .catalog import INIT_STRATEGY_OPTIONS, OPTIMIZER_ACQ_OPTIONS, SINGLE_OBJECTIVE_OPTIONS, MULTI_OBJECTIVE_OPTIONS
from .templates import build_single_campaign_template, build_multi_campaign_template, variables_as_tuples
from core.hardware.process_adapters import DEFAULT_PROCESS_ADAPTER
from core.hardware.uvvis_measurement import measurement_settings, measurement_session_updates


REPRO_FIELDS = {
    "enabled": ("enabled", False), "method": ("method", "LHS"),
    "n_points": ("n_points", 8), "patterns": ("patterns", ["Immediate (A->A)", "Bracketed (A->B->A)"]),
    "cyclic_repeats": ("cyclic_repeats", 3), "use_sentinel": ("use_sentinel", True),
    "sentinel_every_n_runs": ("sentinel_every", 5), "escalate_cleaning": ("escalate_cleaning", True),
    "max_cleaning_level": ("max_cleaning", 2), "block_on_fail": ("block_on_fail", True),
    "outlier_repeat_enabled": ("outlier_repeat_enabled", False),
    "outlier_pair_rsd_pct": ("outlier_pair_rsd_pct", 5.0),
    "outlier_gap_pct": ("outlier_gap_pct", 10.0),
    "outlier_downgrade_conditional": ("outlier_downgrade_conditional", True),
}

HARDWARE_DEFAULTS = {
    "simulation_mode": "off", "opc_url": "http://em-nun:57080", "use_autosampler": False,
    "volume_to_collect": 3.0, "process_adapter": DEFAULT_PROCESS_ADAPTER,
    "process_adapter_config": {}, "running_protocol_script": None,
    "measurement_source_prefix": "OpusOPCSvr.HP-CZC3484P17->", "measurement_source_signal": "PDA - mM",
}


def widget_state_updates(mode, state):
    """Restore explicit widget keys before rendering, including already edited forms."""
    fields = {
        "campaign_name": "run_name", "campaign_date": "experiment_date", "campaign_notes": "experiment_notes",
        "initial_experiments_input": "initial_experiments", "total_iterations_input": "total_iterations",
        "simulation_mode_select": "simulation_mode", "opc_url_input": "opc_url",
        "use_autosampler_input": "use_autosampler", "volume_to_collect_input": "volume_to_collect",
        "acq_func_select": "acq_func", "init_strategy_select": "init_strategy", "random_seed_input": "random_seed",
    }
    fields["response_select" if mode == "single" else "objectives_select"] = (
        "response_to_optimize" if mode == "single" else "objectives"
    )
    return {f"{mode}_{widget}": state[key] for widget, key in fields.items() if key in state}


def build_setup_payload(mode, name, state, experiment_name, experiment_date, notes):
    if mode not in ("single", "multi"):
        raise ValueError("Unknown campaign type.")
    optimization = {
        key: state.get(key, default) for key, default in {
            "initial_experiments": 5, "total_iterations": 20, "acq_func": "EI",
            "init_strategy": "Random", "random_seed": 42, "initial_sort_by": "Do not sort",
            "initial_sort_direction": "Ascending",
        }.items()
    }
    optimization.update(
        reference_categories=dict(state.get(f"{mode}_reference_categories", {})),
        bo_qc_sentinel_enabled=bool(state.get(f"{mode}_bo_qc_enabled", False)),
        bo_qc_sentinel_every_n_runs=int(state.get(f"{mode}_bo_qc_every_n_runs", 5)),
        ui_mode=state.get(f"{mode}_ui_mode", "Quick"),
        reproducibility={field: state.get(f"{mode}_repro_{suffix}", default)
                         for field, (suffix, default) in REPRO_FIELDS.items()},
    )
    if mode == "single":
        optimization["response"] = state.get("response_to_optimize", SINGLE_OBJECTIVE_OPTIONS[0])
        builder = build_single_campaign_template
    else:
        objectives = list(state.get("objectives", []))
        optimization["objectives"] = objectives
        optimization["objective_directions"] = {obj: state.get(f"{obj}_direction", "maximize") for obj in objectives}
        optimization["reproducibility"]["objective"] = state.get("multi_repro_objective", "")
        builder = build_multi_campaign_template
    hardware = {key: state.get(key, default) for key, default in HARDWARE_DEFAULTS.items()}
    hardware.update(measurement_settings(state))
    payload = builder(name, state.get("variables", []), optimization, hardware)
    payload["metadata"] = {
        "experiment_name": experiment_name or name,
        "experiment_date": experiment_date.isoformat(), "notes": str(notes),
    }
    return payload


def setup_session_updates(payload, mode):
    """Validate before changing any widget or run state; accept legacy templates."""
    if mode not in ("single", "multi") or not isinstance(payload, dict) or payload.get("mode") != mode:
        raise ValueError(f"Select a {mode}-objective campaign setup.")
    variables = variables_as_tuples(payload.get("variables", []))
    if len(variables) != len(payload.get("variables", [])):
        raise ValueError("The saved setup contains invalid variable definitions.")
    optimization, hardware = payload.get("optimization", {}), payload.get("hardware", {})
    if not isinstance(optimization, dict) or not isinstance(hardware, dict):
        raise ValueError("The saved campaign settings are invalid.")
    updates = {"variables": variables, **{key: hardware.get(key, default) for key, default in HARDWARE_DEFAULTS.items()}}
    for key, default, minimum, maximum in (
        ("initial_experiments", 5, 1, 100), ("total_iterations", 20, 1, 100),
        ("random_seed", 42, 0, 2147483647),
    ):
        value = int(optimization.get(key, default))
        if not minimum <= value <= maximum:
            raise ValueError(f"Saved {key} is outside the supported range.")
        updates[key] = value
    for key, choices in (("acq_func", OPTIMIZER_ACQ_OPTIONS), ("init_strategy", INIT_STRATEGY_OPTIONS)):
        value = optimization.get(key, choices[0])
        if value not in choices:
            raise ValueError(f"Saved {key} is not supported.")
        updates[key] = value
    if updates["simulation_mode"] not in ("off", "hybrid", "full"):
        raise ValueError("Saved execution mode is not supported.")
    if mode == "single":
        response = optimization.get("response", SINGLE_OBJECTIVE_OPTIONS[0])
        if response not in SINGLE_OBJECTIVE_OPTIONS:
            raise ValueError("Saved objective is not supported.")
        updates["response_to_optimize"] = response
    else:
        objectives = list(optimization.get("objectives", []))
        if any(obj not in MULTI_OBJECTIVE_OPTIONS for obj in objectives):
            raise ValueError("Saved objective is not supported.")
        updates["objectives"] = objectives
        directions = optimization.get("objective_directions", {})
        if not isinstance(directions, dict):
            raise ValueError("Saved objective directions are invalid.")
        for obj in objectives:
            direction = directions.get(obj, "maximize")
            if direction not in ("maximize", "minimize"):
                raise ValueError(f"Invalid saved direction for {obj}.")
            updates[f"{obj}_direction"] = direction
    repro = optimization.get("reproducibility", {})
    if not isinstance(repro, dict):
        raise ValueError("Saved reproducibility settings are invalid.")
    for field, (suffix, default) in REPRO_FIELDS.items():
        updates[f"{mode}_repro_{suffix}"] = repro.get(field, default)
    if mode == "multi":
        updates["multi_repro_objective"] = repro.get("objective", "")
    updates[f"{mode}_bo_qc_enabled"] = bool(optimization.get("bo_qc_sentinel_enabled", False))
    updates[f"{mode}_bo_qc_every_n_runs"] = int(optimization.get("bo_qc_sentinel_every_n_runs", 5))
    references = dict(optimization.get("reference_categories", {}))
    for name, levels, _, _, kind in variables:
        if kind == "categorical":
            value = references.get(name, levels[0])
            if value not in levels:
                raise ValueError(f"Saved sentinel category for {name} is not allowed.")
            references[name] = value
            updates[f"{mode}_reference_{name}"] = value
    updates[f"{mode}_reference_categories"] = references
    updates.update(measurement_session_updates(hardware))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Saved campaign metadata is invalid.")
    updates["run_name"] = str(metadata.get("experiment_name") or payload.get("name", ""))
    updates["experiment_notes"] = str(metadata.get("notes", ""))
    if metadata.get("experiment_date"):
        updates["experiment_date"] = datetime.fromisoformat(metadata["experiment_date"]).date()
    if optimization.get("ui_mode") in ("Quick", "Advanced"):
        updates[f"{mode}_ui_mode"] = optimization["ui_mode"]
    sort_by = optimization.get("initial_sort_by", "Do not sort")
    updates["initial_sort_by"] = sort_by if sort_by in [v[0] for v in variables] else "Do not sort"
    updates["initial_sort_direction"] = optimization.get("initial_sort_direction", "Ascending")
    updates.update(optimization_running=False, iteration=0, experiment_data=[], initial_queue=[],
                   pending_next_params=None, optimizer=None, reused_count=0, runner=None)
    updates[f"{mode}_repro_report"] = None
    updates[f"{mode}_bo_qc_count"] = 0
    updates[f"{mode}_bo_qc_history"] = []
    updates.update(widget_state_updates(mode, updates))
    return updates
