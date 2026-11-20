import json
import csv
import math
import os
import tempfile
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
        results_file="sample_results.csv",
        stability_mode="cv",
        sd_threshold=None,
    ):

        self.reader = reader
        self.calibration_file = calibration_file

        self.n_replicates = n_replicates
        self.replicate_interval = replicate_interval

        self.cv_threshold = cv_threshold
        self.validate_stability_settings(stability_mode, cv_threshold, sd_threshold)
        self.stability_mode = stability_mode
        self.sd_threshold = float(sd_threshold) if sd_threshold is not None else None
        self.max_extra_measurements = max_extra_measurements

        self.results_file = Path(results_file) if results_file is not None else None

        self.slope = None
        self.intercept = None
        self.r2 = None

        self.range_min = None
        self.range_max = None

        self.wavelength = None

        self.load_calibration_model()

    @staticmethod
    def validate_stability_settings(mode, cv_threshold, sd_threshold):
        if mode not in ("cv", "sd", "auto"):
            raise ValueError("UV/VIS stability mode must be cv, sd or auto.")
        if isinstance(cv_threshold, bool) or not math.isfinite(float(cv_threshold)) or float(cv_threshold) < 0:
            raise ValueError("UV/VIS CV limit must be finite and nonnegative.")
        if sd_threshold is None:
            if mode != "cv":
                raise ValueError("Set a maximum absorbance SD (AU) before using SD or automatic stability mode.")
        elif isinstance(sd_threshold, bool) or not math.isfinite(float(sd_threshold)) or float(sd_threshold) <= 0:
            raise ValueError("UV/VIS SD limit must be finite and positive, in AU.")

    def stability_decision(self, mean, sd, cv):
        """Use sample SD near zero; automatic mode applies the larger SD allowance."""
        criterion = self.stability_mode
        if criterion == "auto":
            relative_sd_limit = abs(mean) * float(self.cv_threshold) / 100.0
            criterion = "sd" if self.sd_threshold >= relative_sd_limit else "cv"
        threshold = self.sd_threshold if criterion == "sd" else float(self.cv_threshold)
        value = sd if criterion == "sd" else cv
        accepted = (math.isfinite(mean) and math.isfinite(sd) and sd >= 0
                    and math.isfinite(value) and value <= threshold)
        return {
            "stability_mode": self.stability_mode,
            "stability_criterion": criterion,
            "stability_threshold": threshold,
            "stability_units": "AU" if criterion == "sd" else "%",
            "stability_cv_threshold_pct": float(self.cv_threshold),
            "stability_sd_threshold_au": self.sd_threshold,
            "accepted": accepted,
        }


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

            # Preserve old sample logs when new stability fields expand the schema.
            with self.results_file.open("r", encoding="utf-8-sig", newline="") as existing:
                reader = csv.DictReader(existing)
                old_fields = reader.fieldnames or []
                fields = list(dict.fromkeys(old_fields + list(result)))
                if fields != old_fields:
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                                         dir=self.results_file.parent, delete=False) as output:
                            temporary = Path(output.name)
                            writer = csv.DictWriter(output, fieldnames=fields)
                            writer.writeheader()
                            for previous in reader:
                                if None in previous or any(value is None for value in previous.values()):
                                    raise ValueError("Existing sample results have inconsistent columns.")
                                writer.writerow(previous)
                            writer.writerow(result)
                        existing.close()  # Windows requires the original handle closed before replace.
                        os.replace(temporary, self.results_file)
                        temporary = None
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                    return

            row.reindex(columns=old_fields).to_csv(
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
        # Rolling stability validation
        # -------------------------------------------------

        extra_measurements = 0
        stability = self.stability_decision(mean_absorbance, sd_absorbance, cv_absorbance)


        while (
            not stability["accepted"]
            and
            extra_measurements
            < self.max_extra_measurements
        ):

            print()
            print(
                f"{stability['stability_criterion'].upper()} exceeds "
                f"{stability['stability_threshold']:g} {stability['stability_units']}"
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
            stability = self.stability_decision(mean_absorbance, sd_absorbance, cv_absorbance)


            print(
                f"New measurement: "
                f"{absorbance:.4f} AU"
            )

            print(
                f"Rolling SD: {sd_absorbance:.6g} AU; CV: {cv_absorbance:.2f}%; "
                f"criterion: {stability['stability_criterion'].upper()}"
            )


        # -------------------------------------------------
        # Stability result
        # -------------------------------------------------

        if stability["accepted"]:

            accepted = True

            print()
            print(
                f"Measurement accepted "
                f"({stability['stability_criterion'].upper()} criterion; limit "
                f"{stability['stability_threshold']:g} {stability['stability_units']})"
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

            **stability,

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
