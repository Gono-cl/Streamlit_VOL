"""Mixed campaign regression tests; no hardware is contacted."""

import io
import itertools
import json
import pickle
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.campaigns.templates import (
    build_single_campaign_template, normalize_variables, variables_as_tuples,
)
from core.campaigns.variables import (
    clip_point, expanded_domains, normalize_variable, optimizer_dimensions,
    parameters_from_row, parse_categories, point_from_row, read_campaign_data, reference_point,
    variable_axis, variable_domains,
)
from core.optimization.bayesian_optimization import StepBayesianOptimizer
from core.optimization.initial_design import (
    augmented_lhs_with_reuse, gap_aware_initial_points, lhs_samples, normalize_points,
)
from src.repro.planner import build_initialization_points
from src.repro.repro_engine import ReproducibilityEngine, ReplicatePattern


VARIABLES = [
    ("current_density", 0.9, 1.1, "mA/cm2", "continuous"),
    ("applied_charge", 9.0, 11.0, "F/mol", "continuous"),
    ("substrate_concentration", 90.0, 110.0, "mg/L", "continuous"),
    ("electrolyte_concentration", 0.04, 0.06, "M", "continuous"),
    ("cell", ["EC1", "EC2", "EC3"], None, "", "categorical"),
    ("electrolyte", ["Na2SO4", "NaCl", "NaNO3"], None, "", "categorical"),
]


def assert_valid(point, domains):
    assert len(point) == len(domains)
    for value, domain in zip(point, domains):
        if isinstance(domain, list):
            assert value in domain
            assert isinstance(value, str)
        else:
            assert domain[0] <= value <= domain[1]


def test_legacy_metadata_and_mixed_template_roundtrip():
    assert variables_as_tuples([["temperature", 20, 80, "C"]]) == [
        ("temperature", 20.0, 80.0, "C", "continuous")
    ]
    payload = build_single_campaign_template("MB", VARIABLES, {}, {})
    restored = variables_as_tuples(json.loads(json.dumps(payload))["variables"])
    assert restored == VARIABLES
    assert normalize_variables(restored)[-1]["categories"] == ["Na2SO4", "NaCl", "NaNO3"]


def test_category_input_and_numeric_looking_csv_labels():
    assert parse_categories(" EC1, EC2, ,EC1, EC3 ") == ["EC1", "EC2", "EC3"]
    with pytest.raises(ValueError, match="at least one"):
        parse_categories(" , , ")
    with pytest.raises(ValueError, match="finite"):
        normalize_variable(("x", 0, float("inf"), "", "continuous"))
    variables = [("code", ["1", "2"], None, "", "categorical")]
    csv_row = pd.read_csv(io.StringIO("code\n1\n")).iloc[0]
    assert point_from_row(csv_row, variables) == ["1"]
    assert point_from_row({"code": 1.0}, variables, allow_new_categories=True) == ["1"]
    with pytest.raises(ValueError, match="Unknown category"):
        point_from_row({"code": "other"}, variables)


def test_csv_labels_keep_leading_zeros_and_na_strings():
    variables = [("code", ["01", "1", "NA"], None, "", "categorical")]
    frame = read_campaign_data(io.StringIO("code,response\n01,1\n1,2\nNA,3\n"), variables)
    assert frame["code"].tolist() == ["01", "1", "NA"]
    assert frame["response"].tolist() == [1, 2, 3]


@pytest.mark.parametrize("method", ["LHS", "Random", "augmented"])
def test_mixed_initial_design_is_reproducible_and_protocol_compatible(method):
    from running_protocols.MB_6_variables import _compute_operating_point

    domains = variable_domains(VARIABLES)
    reused = [list(reference_point(VARIABLES).values())]

    def generate():
        rng = np.random.default_rng(123)
        if method == "augmented":
            return augmented_lhs_with_reuse(domains, 10, rng, reused_points=reused, trials=3)
        return gap_aware_initial_points(domains, 9, rng, existing_points=reused, method=method)

    points = generate()
    assert len(points) == 9
    assert generate() == points
    for point in points:
        assert_valid(point, domains)
        params = dict(zip((v[0] for v in VARIABLES), point))
        operating = _compute_operating_point(params)
        assert operating["cell"] == params["cell"]
        assert operating["electrolyte"] == params["electrolyte"]


def test_categorical_distance_does_not_depend_on_label_order():
    domains = [["EC1", "EC2", "EC3"]]
    points = normalize_points([["EC1"], ["EC2"], ["EC3"]], domains)
    distances = [np.linalg.norm(points[i] - points[j]) for i, j in itertools.combinations(range(3), 2)]
    assert distances == pytest.approx([1, 1, 1])


def test_reuse_domain_expansion_and_campaign_restriction():
    variables = [("temperature", 20, 40, "C", "continuous"),
                 ("cell", ["EC1", "EC2"], None, "", "categorical")]
    reused = pd.DataFrame([{"Select": True, "temperature": 50, "cell": "EC3"},
                           {"Select": False, "temperature": 100, "cell": "EC4"}])
    model = expanded_domains(variables, reused)
    assert model == [(20.0, 50.0), ["EC1", "EC2", "EC3"]]
    assert variable_domains(variables) == [(20.0, 40.0), ["EC1", "EC2"]]
    assert clip_point([50, "EC3"], variable_domains(variables)) == [40, "EC1"]
    assert parameters_from_row(reused.iloc[0], variables, allow_new_categories=True)["cell"] == "EC3"
    for generate in (
        lambda: augmented_lhs_with_reuse(variable_domains(variables), 3, np.random.default_rng(3),
                                         reused_points=[[50, "EC3"]], trials=2),
        lambda: gap_aware_initial_points(variable_domains(variables), 2, np.random.default_rng(3),
                                         existing_points=[[50, "EC3"]]),
    ):
        points = generate()
        assert len(points) == 2
        for point in points:
            assert_valid(point, variable_domains(variables))


def test_single_optimizer_model_suggestion_and_pickle_resume():
    variables = [VARIABLES[0], VARIABLES[-2], VARIABLES[-1]]
    domains = variable_domains(variables)
    opt = StepBayesianOptimizer(optimizer_dimensions(variables), suggest_bounds=domains)
    for point in lhs_samples(domains, 6, np.random.default_rng(1)):
        response = float(point[0]) + (point[1] == "EC2") + (point[2] == "NaCl")
        opt.observe(point, -response)
    suggested = opt.suggest()
    assert_valid(suggested, domains)
    resumed = pickle.loads(pickle.dumps(opt))
    assert_valid(resumed.suggest(), domains)
    resumed.observe(suggested, -1.0)
    assert resumed.x_iters[-1][-1] in domains[-1]


def test_multi_optimizer_uses_native_categorical_dimensions():
    from core.optimization.multiobjective import build_multiobjective_optimizer

    variables = [VARIABLES[0], VARIABLES[-2], VARIABLES[-1]]
    domains = variable_domains(variables)
    opt = build_multiobjective_optimizer(optimizer_dimensions(variables, backend="ProcessOptimizer"),
                    n_objectives=2, n_initial_points=0, acq_optimizer="sampling", random_state=4)
    for point in lhs_samples(domains, 6, np.random.default_rng(1)):
        opt.tell(point, [-float(point[0]) - (point[1] == "EC2"),
                         float(point[0]) - (point[2] == "NaCl")])
    assert_valid(clip_point(opt.ask(), domains), domains)
    resumed = pickle.loads(pickle.dumps(opt))
    assert_valid(clip_point(resumed.ask(), domains), domains)


def test_category_only_multiobjective_campaign_can_fit_and_resume():
    from core.optimization.multiobjective import build_multiobjective_optimizer

    variables = VARIABLES[-2:]
    domains = variable_domains(variables)
    opt = build_multiobjective_optimizer(optimizer_dimensions(variables, backend="ProcessOptimizer"),
                                         n_objectives=2, n_initial_points=0,
                                         acq_optimizer="sampling", random_state=3)
    points = [["EC1", "NaCl"], ["EC2", "NaNO3"], ["EC3", "Na2SO4"]]
    # Exercise the native optimizer's exploration route with the RF fallback.
    with patch("numpy.random.uniform", return_value=0.1):
        opt.tell(points, [[1.0, 2.0], [2.0, 1.0], [1.5, 1.5]])
    assert_valid(opt.ask(), domains)
    restored = pickle.loads(pickle.dumps(opt))
    assert_valid(restored.ask(), domains)


@pytest.mark.parametrize("method", ["lhs", "corners_center"])
def test_reproducibility_and_csv_resume_preserve_categories(tmp_path, method):
    variables = [VARIABLES[0], VARIABLES[-2], VARIABLES[-1]]
    bounds = dict(zip((v[0] for v in variables), variable_domains(variables)))
    points = build_initialization_points(bounds, 6, method=method)
    reference = reference_point(variables, {"cell": "EC2", "electrolyte": "NaCl"})
    assert reference["cell"] == "EC2"
    received = []

    def runner(point, metadata):
        received.append(dict(point))
        return {"objective": point["current_density"] + (point["cell"] == "EC2")}

    engine = ReproducibilityEngine(runner)
    engine.schedule_with_reproducibility(points, patterns=[ReplicatePattern.IMMEDIATE],
                                         sentinel_point=reference, sentinel_every_n_runs=3)
    report = engine.analyze()
    assert report["metrics"]["immediate_noise_sigma"] == pytest.approx(0)
    path = tmp_path / "repro.csv"
    frame = engine.save_csv_logs(path)
    restored = ReproducibilityEngine(runner)
    restored.load_csv_logs(path)
    assert [r.point.values for r in restored.records] == [r.point.values for r in engine.records]
    assert set(frame["x_cell"]) <= {"EC1", "EC2", "EC3"}
    assert received and all(isinstance(p["electrolyte"], str) for p in received)


def test_category_charts_use_nominal_axes():
    assert variable_axis(VARIABLES[-2]).to_dict()["type"] == "nominal"
    assert variable_axis(VARIABLES[0]).to_dict()["type"] == "quantitative"


def test_mb_all_cell_electrolyte_combinations_accept_named_choices():
    from running_protocols.MB_6_variables import _compute_operating_point

    for cell, electrolyte in itertools.product(VARIABLES[-2][1], VARIABLES[-1][1]):
        params = reference_point(VARIABLES, {"cell": cell, "electrolyte": electrolyte})
        operating = _compute_operating_point(params)
        assert operating["cell"] == cell
        assert operating["electrolyte"] == electrolyte
