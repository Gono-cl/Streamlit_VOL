import time
import statistics


class CalibrationExperiment:

    def __init__(
        self,
        pumps,
        reader,
        stock_concentration,
        total_flow,
        stock_pump="PUMP1",
        water_pump="PUMP2",
        target_wavelength=664.0,
        wait_time=60,
        n_replicates=5,
        replicate_interval=5.0,
        cv_threshold=5.0,
        max_extra_measurements=10
    ):

        self.pumps = pumps
        self.reader = reader

        self.stock_concentration = stock_concentration
        self.total_flow = total_flow

        self.stock_pump = stock_pump
        self.water_pump = water_pump

        self.target_wavelength = target_wavelength
        self.wait_time = wait_time

        self.n_replicates = n_replicates
        self.replicate_interval = replicate_interval

        self.cv_threshold = cv_threshold
        self.max_extra_measurements = max_extra_measurements

        self.results = []


    def calculate_flows(
        self,
        target_concentration
    ):
        """
        Calculate stock and water flow rates
        required for the desired concentration.
        """

        if target_concentration < 0:
            raise ValueError(
                "Target concentration cannot be negative."
            )

        if target_concentration > self.stock_concentration:
            raise ValueError(
                "Target concentration cannot be higher "
                "than the stock concentration."
            )

        q_stock = (
            target_concentration
            / self.stock_concentration
            * self.total_flow
        )

        q_water = (
            self.total_flow
            - q_stock
        )

        return q_stock, q_water


    def calculate_statistics(
        self,
        absorbance_values
    ):
        """
        Calculate mean, standard deviation and CV.
        """

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
            cv_absorbance = 0.0

        return (
            mean_absorbance,
            sd_absorbance,
            cv_absorbance
        )


    def run_condition(
        self,
        target_concentration
    ):
        """
        Run one calibration standard.

        Workflow:
        1. Calculate flow rates.
        2. Set pump flows.
        3. Wait for the new concentration.
        4. Collect initial replicate spectra.
        5. Calculate mean, SD and CV.
        6. If CV > threshold:
           - acquire one additional spectrum
           - discard oldest spectrum
           - recalculate using latest 5 spectra
        7. Repeat until CV is acceptable or the
           maximum number of extra measurements is reached.
        8. Store the final result.
        """

        q_stock, q_water = self.calculate_flows(
            target_concentration
        )

        print()
        print("----------------------------------------")

        print(
            f"Target concentration: "
            f"{target_concentration:.2f} mg/L"
        )

        print(
            f"{self.stock_pump}: "
            f"{q_stock:.3f} mL/min"
        )

        print(
            f"{self.water_pump}: "
            f"{q_water:.3f} mL/min"
        )

        # -------------------------------------------------
        # Set pump flows
        # -------------------------------------------------

        self.pumps.set_flow(
            self.stock_pump,
            q_stock
        )

        self.pumps.set_flow(
            self.water_pump,
            q_water
        )

        # -------------------------------------------------
        # Wait for new condition
        # -------------------------------------------------

        print(
            f"Waiting {self.wait_time} seconds "
            f"for the new condition..."
        )

        time.sleep(
            self.wait_time
        )

        # -------------------------------------------------
        # Initial replicate measurements
        # -------------------------------------------------

        absorbance_values = []
        wavelength_values = []
        files_used = []

        print()
        print(
            f"Collecting {self.n_replicates} "
            f"initial replicate spectra..."
        )

        for replicate in range(
            self.n_replicates
        ):

            (
                latest_file,
                measured_wavelength,
                absorbance
            ) = self.reader.get_latest_absorbance(
                self.target_wavelength
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
                f"{absorbance:.4f} AU "
                f"at {measured_wavelength:.2f} nm"
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
            extra_measurements < self.max_extra_measurements
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
                self.target_wavelength
            )

            # Add newest measurement
            absorbance_values.append(
                absorbance
            )

            wavelength_values.append(
                measured_wavelength
            )

            files_used.append(
                str(latest_file)
            )

            # Keep only the latest N measurements
            absorbance_values = \
                absorbance_values[
                    -self.n_replicates:
                ]

            wavelength_values = \
                wavelength_values[
                    -self.n_replicates:
                ]

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
                f"Rolling mean: "
                f"{mean_absorbance:.4f} AU"
            )

            print(
                f"Rolling SD: "
                f"{sd_absorbance:.4f} AU"
            )

            print(
                f"Rolling CV: "
                f"{cv_absorbance:.2f} %"
            )

        # -------------------------------------------------
        # Final validation
        # -------------------------------------------------

        if (
            cv_absorbance
            <= self.cv_threshold
        ):

            point_accepted = True

            print()
            print(
                f"Point accepted "
                f"(CV = "
                f"{cv_absorbance:.2f}%)"
            )

        else:

            point_accepted = False

            print()
            print(
                "Point failed stability check."
            )

            print(
                f"Final CV = "
                f"{cv_absorbance:.2f}%"
            )

            print(
                f"Maximum extra measurements "
                f"reached: "
                f"{self.max_extra_measurements}"
            )

        # -------------------------------------------------
        # Mean wavelength
        # -------------------------------------------------

        mean_wavelength = statistics.mean(
            wavelength_values
        )

        # -------------------------------------------------
        # Store final result
        # -------------------------------------------------

        result = {

            "target_concentration":
                target_concentration,

            "stock_flow":
                q_stock,

            "water_flow":
                q_water,

            "wavelength":
                mean_wavelength,

            "absorbance":
                mean_absorbance,

            "absorbance_sd":
                sd_absorbance,

            "absorbance_cv":
                cv_absorbance,

            "accepted":
                point_accepted,

            "extra_measurements":
                extra_measurements,

            "replicates":
                absorbance_values.copy(),

            "files":
                files_used
        }

        self.results.append(
            result
        )

        # -------------------------------------------------
        # Final display
        # -------------------------------------------------

        print()
        print(
            f"Final mean absorbance: "
            f"{mean_absorbance:.4f} AU"
        )

        print(
            f"Final SD: "
            f"{sd_absorbance:.4f} AU"
        )

        print(
            f"Final CV: "
            f"{cv_absorbance:.2f} %"
        )

        print(
            f"Additional measurements: "
            f"{extra_measurements}"
        )

        return result


    def run_calibration(
        self,
        concentrations
    ):
        """
        Run all calibration standards.
        """

        self.results = []

        try:

            for concentration in concentrations:

                self.run_condition(
                    concentration
                )

        finally:

            print()
            print(
                "Stopping pumps..."
            )

            self.pumps.stop_all()

        return self.results