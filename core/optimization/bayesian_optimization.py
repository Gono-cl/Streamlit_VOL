from skopt import Optimizer
from skopt.space import Space


class StepBayesianOptimizer:
    def __init__(self, variables, base_estimator="GP", acq_func="EI", random_state=42, suggest_bounds=None, n_initial_points=0):
        self.variable_names = [dim.name for dim in variables]
        self.space = Space(variables)
        self._optimizer = Optimizer(
            dimensions=self.space,
            base_estimator=base_estimator,
            acq_func=acq_func,
            random_state=random_state,
            # We manage the initial design externally (reuse + LHS/Random queue)
            n_initial_points=n_initial_points
        )
        # suggest_bounds: list of (low, high) for clipping suggestions to campaign bounds
        self.suggest_bounds = suggest_bounds
        self.x_iters = []
        self.y_iters = []

    def suggest(self):
        x = self._optimizer.ask()
        if self.suggest_bounds is not None:
            x = [min(max(val, low), high) for val, (low, high) in zip(x, self.suggest_bounds)]
        return x

    def observe(self, x, y):
        self._optimizer.tell(x, y)
        self.x_iters.append(x)
        self.y_iters.append(y)

    def set_suggest_bounds(self, suggest_bounds):
        self.suggest_bounds = suggest_bounds

    @property
    def skopt_optimizer(self):
        return self._optimizer
