import json
import time
import statistics
from datetime import datetime
from pathlib import Path

import pandas as pd


class SampleMeasurement:

    def __init__(
        self,
        reader,
        calibration_file="calibration_model.json",
        n_replicates=5,
        replicate_interval=2.0,
        cv_threshold=5.0,
        max_extra_measurements=10,
        results_file="sample_results.csv"
    ):

        self.reader = reader
        self.calibration_file = calibration_file

        self.n_replicates = n_replicates
        self.replicate_interval = replicate_interval

        self.cv_threshold = cv_threshold
        self.max_extra_measurements = max_extra_measurements

        self.results_file = Path(results_file) if results_file is not None else None

        self.slope = None
        self.intercept = None
        self.r2 = None

        self.range_min = None
        self.range_max = None

        self.wavelength = None

        self.load_calibration_model()


    # =====================================================
    # LOAD CALIBRATION
    # =====================================================

    def load_calibration_model(self):

        with open(
            self.calibration_file,
            "r",
            encoding="utf-8"
        ) as file:

            model = json.load(file)

        self.calibration_model = model
        self.slope = model["slope"]
        self.intercept = model["intercept"]
        self.r2 = model["r2"]

        self.range_min = model["range_min"]
        self.range_max = model["range_max"]

        self.wavelength = model["wavelength"]


    # =====================================================
    # CALCULATE CONCENTRATION
    # =====================================================

    def calculate_concentration(
        self,
        absorbance
    ):

        concentration = (
            absorbance
            - self.intercept
        ) / self.slope

        return concentration


    # =====================================================
    # CHECK CALIBRATION RANGE
    # =====================================================

    def check_calibration_range(
        self,
        concentration
    ):

        if concentration < self.range_min:

            return "below_range"

        elif concentration > self.range_max:

            return "above_range"

        else:

            return "valid"


    # =====================================================
    # STATISTICS
    # =====================================================

    def calculate_statistics(
        self,
        absorbance_values
    ):

        mean_absorbance = statistics.mean(
            absorbance_values
        )

        if len(absorbance_values) > 1:

            sd_absorbance = statistics.stdev(
                absorbance_values
            )

        else:

            sd_absorbance = 0.0


        if mean_absorbance != 0:

            cv_absorbance = (
                sd_absorbance
                / abs(mean_absorbance)
                * 100
            )

        else:

            cv_absorbance = 0.0 if sd_absorbance == 0 else float("inf")


        return (
            mean_absorbance,
            sd_absorbance,
            cv_absorbance
        )


    # =====================================================
    # SAVE RESULT
    # =====================================================

    def save_result(
        self,
        result
    ):

        if self.results_file is None:
            return

        row = pd.DataFrame(
            [result]
        )

        if self.results_file.exists():

            row.to_csv(
                self.results_file,
                mode="a",
                header=False,
                index=False
            )

        else:

            row.to_csv(
                self.results_file,
                index=False
            )

        print(
            f"Result saved to: "
            f"{self.results_file}"
        )


    # =====================================================
    # MEASURE SAMPLE
    # =====================================================

    def measure_sample(self):

        print()
        print(
            "Collecting sample measurements..."
        )

        absorbance_values = []
        wavelength_values = []
        files_used = []


        # -------------------------------------------------
        # Initial 5 measurements
        # -------------------------------------------------

        for replicate in range(
            self.n_replicates
        ):

            (
                latest_file,
                measured_wavelength,
                absorbance
            ) = self.reader.get_latest_absorbance(
                self.wavelength
            )

            absorbance_values.append(
                absorbance
            )

            wavelength_values.append(
                measured_wavelength
            )

            files_used.append(
                str(latest_file)
            )

            print(
                f"Replicate "
                f"{replicate + 1}/"
                f"{self.n_replicates}: "
                f"{absorbance:.4f} AU"
            )

            if replicate < self.n_replicates - 1:

                time.sleep(
                    self.replicate_interval
                )


        # -------------------------------------------------
        # Initial statistics
        # -------------------------------------------------

        (
            mean_absorbance,
            sd_absorbance,
            cv_absorbance
        ) = self.calculate_statistics(
            absorbance_values
        )

        print()
        print(
            f"Initial mean: "
            f"{mean_absorbance:.4f} AU"
        )

        print(
            f"Initial SD: "
            f"{sd_absorbance:.4f} AU"
        )

        print(
            f"Initial CV: "
            f"{cv_absorbance:.2f} %"
        )


        # -------------------------------------------------
        # Rolling CV validation
        # -------------------------------------------------

        extra_measurements = 0


        while (
            cv_absorbance > self.cv_threshold
            and
            extra_measurements
            < self.max_extra_measurements
        ):

            print()
            print(
                f"CV > "
                f"{self.cv_threshold:.1f}%"
            )

            print(
                "Taking one additional measurement..."
            )

            time.sleep(
                self.replicate_interval
            )

            (
                latest_file,
                measured_wavelength,
                absorbance
            ) = self.reader.get_latest_absorbance(
                self.wavelength
            )

            absorbance_values.append(
                absorbance
            )

            wavelength_values.append(
                measured_wavelength
            )

            files_used.append(
                str(latest_file)
            )


            # Keep only latest 5 values
            absorbance_values = (
                absorbance_values[
                    -self.n_replicates:
                ]
            )

            wavelength_values = (
                wavelength_values[
                    -self.n_replicates:
                ]
            )


            extra_measurements += 1


            (
                mean_absorbance,
                sd_absorbance,
                cv_absorbance
            ) = self.calculate_statistics(
                absorbance_values
            )


            print(
                f"New measurement: "
                f"{absorbance:.4f} AU"
            )

            print(
                f"Rolling CV: "
                f"{cv_absorbance:.2f} %"
            )


        # -------------------------------------------------
        # Stability result
        # -------------------------------------------------

        if (
            cv_absorbance
            <= self.cv_threshold
        ):

            accepted = True

            print()
            print(
                f"Measurement accepted "
                f"(CV = "
                f"{cv_absorbance:.2f}%)"
            )

        else:

            accepted = False

            print()
            print(
                "Measurement failed "
                "stability criterion."
            )


        # -------------------------------------------------
        # Calculate concentration
        # -------------------------------------------------

        concentration = (
            self.calculate_concentration(
                mean_absorbance
            )
        )


        # -------------------------------------------------
        # Check calibration range
        # -------------------------------------------------

        status = (
            self.check_calibration_range(
                concentration
            )
        )


        # -------------------------------------------------
        # Mean wavelength
        # -------------------------------------------------

        mean_wavelength = statistics.mean(
            wavelength_values
        )


        # -------------------------------------------------
        # Create result
        # -------------------------------------------------

        result = {

            "timestamp":
                datetime.now().isoformat(
                    timespec="seconds"
                ),

            "wavelength":
                mean_wavelength,

            "absorbance":
                mean_absorbance,

            "absorbance_sd":
                sd_absorbance,

            "absorbance_cv":
                cv_absorbance,

            "concentration":
                concentration,

            "status":
                status,

            "accepted":
                accepted,

            "extra_measurements":
                extra_measurements,

            "calibration_slope":
                self.slope,

            "calibration_intercept":
                self.intercept,

            "calibration_r2":
                self.r2,

            "replicates": absorbance_values.copy(),
            "files": files_used.copy()
        }


        # -------------------------------------------------
        # Save measurement
        # -------------------------------------------------

        self.save_result(
            result
        )


        return result
