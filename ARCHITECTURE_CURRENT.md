# Current Repository Architecture

This is the current runtime architecture centered on `main.py`.

```mermaid
flowchart LR
    A["main.py<br/>(Streamlit router)"]

    subgraph Pages["UI Pages"]
        P1["Home.py"]
        P2["Single_Objective.py"]
        P3["Multi_Objective.py"]
        P4["DoE_Executor.py"]
        P5["Process_Builder.py"]
        P6["Data_Analysis.py"]
        P7["preview_run.py"]
        P8["experiment_database.py"]
    end

    A --> P1
    A --> P2
    A --> P3
    A --> P4
    A --> P5
    A --> P6
    A --> P7
    A --> P8

    subgraph Core["Core Domain Modules"]
        C1["core/campaigns<br/>(catalog + template persistence)"]
        C2["core/optimization<br/>(StepBayesianOptimizer + initial design)"]
        C3["src/repro<br/>(reproducibility planner + engine + policies)"]
        C4["core/hardware/experimental_run.py<br/>(ExperimentRunner orchestration)"]
        C5["core/hardware/process_adapters.py<br/>(adapter registry + runtime prep)"]
        C6["core/hardware/process_profiles.py<br/>(profile CRUD)"]
        C7["core/hardware/opc_communication.py<br/>(OPC REST client)"]
        C8["core/objectives.py<br/>(objective calculations)"]
        C9["core/utils/db_handler.py<br/>(SQLite persistence)"]
        C10["core/utils/export_tools.py"]
        C11["core/utils/logger.py"]
    end

    P2 --> C1
    P2 --> C2
    P2 --> C3
    P2 --> C4
    P2 --> C9
    P2 --> C10
    P2 --> C11

    P3 --> C1
    P3 --> C2
    P3 --> C3
    P3 --> C4
    P3 --> C9
    P3 --> C10
    P3 --> C11

    P4 --> C1
    P4 --> C4
    P4 --> C5
    P4 --> C6
    P4 --> C9
    P4 --> C10
    P4 --> C11

    P5 --> C5
    P5 --> C6
    P6 --> C10
    P8 --> C9

    C4 --> C5
    C4 --> C7
    C4 --> C8
    C5 --> C8

    subgraph Data["Persistence / Artifacts"]
        D1["experiments.db"]
        D2["resumable_runs/"]
        D3["resumable_multiobjective_runs/"]
        D4["resumable_doe_executor_runs/"]
        D5["resumable_manual_runs/"]
        D6["raw_measurements/"]
        D7["campaign_templates/"]
        D8["process_profiles/"]
    end

    C9 --> D1
    P2 --> D2
    P3 --> D3
    P4 --> D4
    P6 --> D2
    P6 --> D3
    P6 --> D5
    C4 --> D6
    C1 --> D7
    C6 --> D8

    subgraph External["External Systems"]
        E1["OPC REST endpoint"]
        E2["Lab hardware + autosampler"]
    end

    C7 --> E1
    C4 --> E2
```

## Cleanup Applied

The following non-runtime/unused files were removed:

- `DoE.py`
- `faq.py`
- `custom_workflow.py`
- `BO_classroom.py`
- `Training/` notebook/training pages
- `examples/repro_demo.py`
- `core/optimization/doe.py`
- `core/gui/__init__.py`
- `core/gui/ui_helpers.py`
- `core/utils/acquisition_viz.py`
- `core/utils/generate_report.py`
- `core/utils/gui_helpers.py`
- `core/utils/gui_labels.py`
- `data/default_variables.py`

## One Portability Risk

- `main.py` points to `"data_analysis.py"` while the file is `Data_Analysis.py`. This works on Windows but can fail on case-sensitive filesystems.
