import json
from pathlib import Path

from PumpController.set_flow import PumpController

from Spectrometer.spectragryph_reader import (
    SpectragryphReader
)

from CalibrationExperiment.calibration_experiment import (
    CalibrationExperiment
)

from DataAnalysis.data_analysis import (
    DataAnalysis
)

from SampleMeasurement.sample_measurement import (
    SampleMeasurement
)


# =========================================================
# CONFIGURATION
# =========================================================

CONFIG_FILE = "config.json"


def load_config():

    config_path = Path(CONFIG_FILE)

    if not config_path.exists():

        raise FileNotFoundError(
            f"Configuration file '{CONFIG_FILE}' not found.\n"
            f"Create it from 'config.example.json' "
            f"and adapt the settings to your system."
        )

    with open(
        config_path,
        "r",
        encoding="utf-8"
    ) as file:

        config = json.load(file)

    return config


# =========================================================
# CREATE SHARED OBJECTS
# =========================================================

def create_pump_controller(config):

    pumps = PumpController(

        host=
        config["opc"]["host"],

        opc_area=
        config["opc"]["area"]
    )

    return pumps


def create_spectragryph_reader(config):

    reader = SpectragryphReader(

        folder=
        config["spectragryph"]["spectra_folder"],

        file_pattern=
        config["spectragryph"]["file_pattern"]
    )

    return reader


# =========================================================
# CALIBRATION
# =========================================================

def run_calibration(
    pumps,
    reader,
    config
):

    calibration_config = (
        config["calibration"]
    )

    pump_config = (
        config["pumps"]
    )

    output_config = (
        config["output"]
    )


    print()
    print(
        "============================================"
    )

    print(
        "AUTOMATED INLINE CALIBRATION"
    )

    print(
        "============================================"
    )


    experiment = CalibrationExperiment(

        pumps=pumps,

        reader=reader,

        stock_concentration=
        calibration_config[
            "stock_concentration"
        ],

        total_flow=
        calibration_config[
            "total_flow"
        ],

        stock_pump=
        pump_config[
            "stock_pump"
        ],

        water_pump=
        pump_config[
            "water_pump"
        ],

        target_wavelength=
        calibration_config[
            "target_wavelength"
        ],

        wait_time=
        calibration_config[
            "wait_time"
        ],

        n_replicates=
        calibration_config[
            "n_replicates"
        ],

        replicate_interval=
        calibration_config[
            "replicate_interval"
        ],

        cv_threshold=
        calibration_config[
            "cv_threshold"
        ],

        max_extra_measurements=
        calibration_config[
            "max_extra_measurements"
        ]
    )


    # -----------------------------------------------------
    # Show calibration settings
    # -----------------------------------------------------

    print()
    print(
        "Calibration settings"
    )

    print(
        "--------------------------------------------"
    )

    print(
        f"Stock concentration: "
        f"{calibration_config['stock_concentration']:.2f} mg/L"
    )

    print(
        f"Total flow: "
        f"{calibration_config['total_flow']:.3f} mL/min"
    )

    print(
        f"Target wavelength: "
        f"{calibration_config['target_wavelength']:.3f} nm"
    )

    print(
        f"Initial replicates: "
        f"{calibration_config['n_replicates']}"
    )

    print(
        f"Replicate interval: "
        f"{calibration_config['replicate_interval']:.1f} s"
    )

    print(
        f"CV threshold: "
        f"{calibration_config['cv_threshold']:.1f}%"
    )

    print()


    # -----------------------------------------------------
    # Show calibration conditions
    # -----------------------------------------------------

    print(
        "Calibration conditions"
    )

    print(
        "--------------------------------------------"
    )


    target_concentrations = (
        calibration_config[
            "target_concentrations"
        ]
    )


    for concentration in target_concentrations:

        q_stock, q_water = (
            experiment.calculate_flows(
                concentration
            )
        )

        print(
            f"{concentration:6.1f} mg/L"
            f"  ->  "
            f"{pump_config['stock_pump']}: "
            f"{q_stock:.3f} mL/min"
            f" | "
            f"{pump_config['water_pump']}: "
            f"{q_water:.3f} mL/min"
        )


    print()

    input(
        "Press ENTER to start calibration..."
    )


    # -----------------------------------------------------
    # Run calibration
    # -----------------------------------------------------

    results = (
        experiment.run_calibration(
            target_concentrations
        )
    )


    # -----------------------------------------------------
    # Analyse calibration
    # -----------------------------------------------------

    analysis = DataAnalysis(
        results
    )


    slope, intercept, r2 = (
        analysis.linear_regression()
    )


    analysis.save_csv(

        output_config[
            "calibration_results_file"
        ]
    )


    analysis.plot_calibration(

        output_config[
            "calibration_plot_file"
        ]
    )


    analysis.save_calibration_model(

        filename=
        output_config[
            "calibration_model_file"
        ],

        wavelength=
        calibration_config[
            "target_wavelength"
        ]
    )


    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    print()
    print(
        "============================================"
    )

    print(
        "CALIBRATION COMPLETED"
    )

    print(
        "============================================"
    )

    print()

    print(
        f"Slope: "
        f"{slope:.6f}"
    )

    print(
        f"Intercept: "
        f"{intercept:.6f}"
    )

    print(
        f"R²: "
        f"{r2:.4f}"
    )

    print()

    print(
        "Files created:"
    )

    print(
        f"  "
        f"{output_config['calibration_results_file']}"
    )

    print(
        f"  "
        f"{output_config['calibration_plot_file']}"
    )

    print(
        f"  "
        f"{output_config['calibration_model_file']}"
    )


# =========================================================
# SAMPLE MEASUREMENT
# =========================================================

def run_sample_measurement(
    reader,
    config
):

    sample_config = (
        config["sample_measurement"]
    )

    output_config = (
        config["output"]
    )


    print()
    print(
        "============================================"
    )

    print(
        "REAL SAMPLE MEASUREMENT"
    )

    print(
        "============================================"
    )


    # -----------------------------------------------------
    # Create SampleMeasurement object
    # -----------------------------------------------------

    try:

        sample = SampleMeasurement(

            reader=reader,

            calibration_file=
            output_config[
                "calibration_model_file"
            ],

            n_replicates=
            sample_config[
                "n_replicates"
            ],

            replicate_interval=
            sample_config[
                "replicate_interval"
            ],

            cv_threshold=
            sample_config[
                "cv_threshold"
            ],

            max_extra_measurements=
            sample_config[
                "max_extra_measurements"
            ],

            results_file=
            output_config[
                "sample_results_file"
            ]
        )

    except FileNotFoundError:

        print()
        print(
            "No calibration model was found."
        )

        print(
            f"Expected file: "
            f"{output_config['calibration_model_file']}"
        )

        print(
            "Run a calibration first or place "
            "a valid calibration model in "
            "the program folder."
        )

        return


    # -----------------------------------------------------
    # Show loaded calibration
    # -----------------------------------------------------

    print()
    print(
        "Calibration model loaded"
    )

    print(
        "--------------------------------------------"
    )

    print(
        f"Slope: "
        f"{sample.slope:.6f}"
    )

    print(
        f"Intercept: "
        f"{sample.intercept:.6f}"
    )

    print(
        f"R²: "
        f"{sample.r2:.4f}"
    )

    print(
        f"Calibration range: "
        f"{sample.range_min:.1f} - "
        f"{sample.range_max:.1f} mg/L"
    )

    print(
        f"Wavelength: "
        f"{sample.wavelength:.3f} nm"
    )

    print()

    print(
        f"Replicates: "
        f"{sample_config['n_replicates']}"
    )

    print(
        f"CV threshold: "
        f"{sample_config['cv_threshold']:.1f}%"
    )


    # -----------------------------------------------------
    # Real sample should already be in flow
    # -----------------------------------------------------

    print()

    input(
        "Ensure the real sample is flowing "
        "through the UV-Vis cell, then press ENTER "
        "to start measurement..."
    )


    # -----------------------------------------------------
    # Measure sample
    # -----------------------------------------------------

    result = (
        sample.measure_sample()
    )


    # -----------------------------------------------------
    # Display result
    # -----------------------------------------------------

    print()
    print(
        "============================================"
    )

    print(
        "SAMPLE RESULT"
    )

    print(
        "============================================"
    )

    print()

    print(
        f"Mean absorbance: "
        f"{result['absorbance']:.4f} AU"
    )

    print(
        f"SD: "
        f"{result['absorbance_sd']:.4f} AU"
    )

    print(
        f"CV: "
        f"{result['absorbance_cv']:.2f} %"
    )

    print(
        f"Calculated concentration: "
        f"{result['concentration']:.2f} mg/L"
    )

    print(
        f"Status: "
        f"{result['status']}"
    )

    print(
        f"Accepted: "
        f"{result['accepted']}"
    )

    print(
        f"Extra measurements: "
        f"{result['extra_measurements']}"
    )


    # -----------------------------------------------------
    # Range interpretation
    # -----------------------------------------------------

    if result["status"] == "valid":

        print()
        print(
            "Sample concentration is inside "
            "the calibration range."
        )


    elif result["status"] == "above_range":

        print()
        print(
            "WARNING:"
        )

        print(
            "Sample concentration is above "
            "the calibration range."
        )

        print(
            "Automatic dilution can be added "
            "in a future development step."
        )


    elif result["status"] == "below_range":

        print()
        print(
            "WARNING:"
        )

        print(
            "Sample concentration is below "
            "the calibration range."
        )


    print()

    print(
        f"Measurement saved to: "
        f"{output_config['sample_results_file']}"
    )


# =========================================================
# MAIN MENU
# =========================================================

def main():

    # -----------------------------------------------------
    # Load configuration
    # -----------------------------------------------------

    try:

        config = load_config()

    except FileNotFoundError as error:

        print()
        print(
            "CONFIGURATION ERROR"
        )

        print(
            "--------------------------------------------"
        )

        print(
            error
        )

        print()
        print(
            "Copy:"
        )

        print(
            "  config.example.json"
        )

        print(
            "to:"
        )

        print(
            "  config.json"
        )

        print(
            "and adapt the values to your system."
        )

        return


    # -----------------------------------------------------
    # Create shared objects
    # -----------------------------------------------------

    pumps = (
        create_pump_controller(
            config
        )
    )

    reader = (
        create_spectragryph_reader(
            config
        )
    )


    # -----------------------------------------------------
    # Main menu
    # -----------------------------------------------------

    while True:

        print()
        print(
            "============================================"
        )

        print(
            "AUTOMATED INLINE ANALYTICS"
        )

        print(
            "============================================"
        )

        print()

        print(
            "1 - Run calibration"
        )

        print(
            "2 - Measure real sample"
        )

        print(
            "3 - Exit"
        )

        print()


        choice = input(
            "Select an option: "
        )


        # -------------------------------------------------
        # Option 1
        # -------------------------------------------------

        if choice == "1":

            run_calibration(
                pumps,
                reader,
                config
            )


        # -------------------------------------------------
        # Option 2
        # -------------------------------------------------

        elif choice == "2":

            run_sample_measurement(
                reader,
                config
            )


        # -------------------------------------------------
        # Option 3
        # -------------------------------------------------

        elif choice == "3":

            print()
            print(
                "Exiting program..."
            )

            try:

                pumps.stop_all()

            except Exception:

                pass

            break


        # -------------------------------------------------
        # Invalid option
        # -------------------------------------------------

        else:

            print()
            print(
                "Invalid option."
            )

            print(
                "Please select 1, 2 or 3."
            )


# =========================================================
# START PROGRAM
# =========================================================

if __name__ == "__main__":
    main()