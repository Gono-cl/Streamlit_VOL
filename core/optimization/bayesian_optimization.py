from skopt import Optimizer
from skopt.space import Space

class StepBayesianOptimizer:
    def __init__(self, variables, base_estimator="GP", acq_func="EI", random_state=42):
        self.variable_names = [dim.name for dim in variables]
        self.space = Space(variables)
        self._optimizer = Optimizer(
            dimensions=self.space,
            base_estimator=base_estimator,
            acq_func=acq_func,
            random_state=random_state
        )
        self.x_iters = []
        self.y_iters = []

    def suggest(self):
        return self._optimizer.ask()

    def observe(self, x, y):
        self._optimizer.tell(x, y)
        self.x_iters.append(x)
        self.y_iters.append(y)

    @property
    def skopt_optimizer(self):
        return self._optimizer


