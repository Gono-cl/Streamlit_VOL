from skopt import gp_minimize
from skopt.space import Real, Integer, Categorical

class BayesianOptimizer:
    def __init__(self, variables, objective_function, n_calls=15, random_state=42):
        self.variables = variables  # e.g., [("temperature", 20, 100), ("res_time", 10, 60)]
        self.objective_function = objective_function
        self.n_calls = n_calls
        self.random_state = random_state

    def run_optimization(self):
        space = []
        for name, lower, upper in self.variables:
            space.append(Real(lower, upper, name=name))

        result = gp_minimize(
            self.objective_function,
            space,
            n_calls=self.n_calls,
            random_state=self.random_state
        )

        best_params = {dim.name: val for dim, val in zip(result.space.dimensions, result.x)}
        best_result = result.fun
        return best_params, best_result
