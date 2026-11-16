# Streamlit_VOL

![App Preview](image.png)

Virtual Optimization Lab (VOL) is a Streamlit application for configuring, running, and reviewing experiment optimization workflows. It combines variable definition, optimizer settings, protocol execution, reproducibility checks, and result management in one interface.

## Features

- Single-objective and multi-objective Bayesian optimization
- Continuous and categorical optimization variables
- Random and Latin hypercube initialization
- Execution of external design-of-experiments (DOE) matrices
- Reusable configuration templates and reuse of previous results
- Configurable protocols, hardware adapters, and measurement integrations
- Real, hybrid, and full-simulation execution modes
- Reproducibility checks and periodic quality-control reference runs
- Stop/resume workflows, saved-run previews, and result exports
- Data analysis, visualization, and an experiment database

## Getting started

Run the following commands from the project folder. Create a Python environment:

```bash
python -m venv .venv
```

Activate the environment using the command for your operating system and shell, then install the dependencies and start VOL:

```bash
python -m pip install -r requirements.txt
python -m streamlit run main.py
```

The application opens in a browser. Use the sidebar to navigate between pages.

## Application pages

| Page | Purpose |
| --- | --- |
| Home | Software overview and getting started |
| Single Objective Optimization | Optimize one selected objective |
| Multi-Objective Optimization | Optimize multiple objectives and inspect trade-offs |
| DOE Executor | Execute an externally prepared experiment matrix |
| Reproducibility Studio | Configure repeatability and reference-run checks |
| Running Protocol Library | Browse protocol files, source code, and optional documentation |
| Data Analysis & Visualization | Review and compare saved results |
| Preview Saved Run | Inspect the configuration, progress, and results of a saved run |
| Experiment Database | Browse stored experiment records |

## Optimization workflow

1. Choose single-objective or multi-objective optimization.
2. Define the variables, their types, and their allowed domains.
3. Select the objectives, optimization directions, initialization strategy, and experiment budget.
4. Configure the execution mode and protocol. Optionally load a template, reuse previous results, or enable reproducibility checks.
5. Start the run and monitor its progress. Saved runs can be resumed, and results can be reviewed or exported through the application.

The optimization pages offer **Quick** and **Advanced** views. Advanced mode exposes additional configuration, template, data-reuse, and initial-design preview tools.

### Variable types

Both optimization pages provide a **Variable Type** selector:

- **Continuous:** enter a lower bound, an upper bound, and an optional unit.
- **Categorical:** enter the allowed choices as a comma-separated list.

Whitespace around categorical choices is trimmed, and duplicate choices are removed. Labels are preserved in suggestions, results, templates, saved runs, and reproducibility logs; the optimizers handle numerical encoding internally. Continuous and categorical variables can be combined in the same optimization.

Existing continuous-only saved runs and templates remain loadable.

### Execution modes

| Mode | Behavior |
| --- | --- |
| Real hardware | Executes the configured hardware workflow and collects measurements |
| Hybrid | Executes the hardware workflow using synthetic measurements |
| Full simulation | Runs the software workflow with synthetic results and skips hardware execution |

### Reproducibility and quality control

Reproducibility Studio provides settings for repeated experiments, reference runs, and acceptance criteria. The optimization pages use those settings when the startup reproducibility gate is enabled. Accepted reproducibility results can contribute initialization data.

Periodic quality-control runs evaluate a fixed reference point during optimization. These checks do not update the optimizer or count toward the optimization iteration budget. Continuous reference values use the midpoint of their bounds; categorical reference values are selected in the interface and saved for resume.

## Protocols and extensions

Protocols are Python modules in `running_protocols/`. The application discovers these files and makes them available through the protocol selector and library. The generic scaffold in `running_protocols/example_protocol.py` can be used when implementing a new workflow.

Protocol hooks can provide descriptive metadata, required parameter names, hardware preparation, objective calculations, sample collection, and cleanup. Default process adapters supply behavior when a corresponding custom hook is not configured.

The execution runner coordinates protocol calls, measurement collection, result handling, and cleanup. Protocol logic can therefore be maintained separately from the optimization pages and optimizer implementations.

## Saved data

VOL stores run metadata, experiment results, optimizer state, and supporting artifacts in local files. Reusable templates store configuration for future runs, while the experiment database supports browsing completed results.

The application provides separate views for resuming a run, previewing saved output, comparing results, and exporting data. Relative paths are resolved from the project folder, so launch the application from that directory.

## Project structure

| Path | Responsibility |
| --- | --- |
| `main.py` | Application entry point and page routing |
| Top-level page modules | Streamlit interfaces for optimization, execution, and analysis |
| `core/campaigns/` | Variable definitions, shared controls, catalogs, and templates |
| `core/optimization/` | Optimizer integration and initial-design generation |
| `core/hardware/` | Execution runner, protocol loading, adapters, and integration code |
| `core/utils/` | Database access, exports, and logging |
| `src/repro/` | Reproducibility planning, execution, and decision policies |
| `running_protocols/` | Configurable workflow modules |
| `campaign_templates/` | Saved configuration templates |
| `resumable_runs/` | Saved single-objective runs |
| `resumable_multiobjective_runs/` | Saved multi-objective runs |
| `resumable_doe_executor_runs/` | Saved DOE execution runs |
| `raw_measurements/` | Supporting measurement artifacts |
| `experiments.db` | Local experiment database |
| `tests/` | Automated software checks |

See [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md) for the runtime architecture.
