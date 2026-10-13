import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import linregress
import json


class DataAnalysis:
    def __init__(self, results):
        self.results = results
        self.df = pd.DataFrame(results)

        self.slope = None
        self.intercept = None
        self.r2 = None

    def linear_regression(self):
        x = self.df["target_concentration"].to_numpy(dtype=float)
        y = self.df["absorbance"].to_numpy(dtype=float)

        fit = linregress(x, y)

        self.slope = fit.slope
        self.intercept = fit.intercept
        self.r2 = fit.rvalue ** 2

        print()
        print("Calibration result")
        print("------------------")

        print(
            f"A = {self.slope:.6f} C "
            f"+ {self.intercept:.6f}"
        )

        print(
            f"R² = {self.r2:.4f}"
        )

        return (
            self.slope,
            self.intercept,
            self.r2
        )

    def save_csv(self, filename="calibration_results.csv"):
        self.df.to_csv(
            filename,
            index=False
        )

        print(
            f"Results saved to: {filename}"
        )

    def plot_calibration(
        self,
        filename="calibration_curve.png"
    ):
        if self.slope is None:
            self.linear_regression()

        x = self.df["target_concentration"].to_numpy(dtype=float)
        y = self.df["absorbance"].to_numpy(dtype=float)

        x_fit = np.linspace(
            x.min(),
            x.max(),
            200
        )

        y_fit = (
            self.slope * x_fit
            + self.intercept
        )

        plt.figure(figsize=(7, 5))

        plt.scatter(
            x,
            y,
            label="Experimental data"
        )

        plt.plot(
            x_fit,
            y_fit,
            label="Linear regression"
        )

        equation = (
            f"A = {self.slope:.6f} C "
            f"+ {self.intercept:.6f}\n"
            f"R² = {self.r2:.4f}"
        )

        plt.text(
            0.05,
            0.95,
            equation,
            transform=plt.gca().transAxes,
            verticalalignment="top"
        )

        plt.xlabel(
            "Methylene blue concentration (mg/L)"
        )

        plt.ylabel(
            "Absorbance"
        )

        plt.title(
            "Methylene Blue Inline Calibration"
        )

        plt.legend()
        plt.tight_layout()

        plt.savefig(
            filename,
            dpi=300
        )

        plt.show()

        print(
            f"Calibration plot saved to: {filename}"
        )
    def save_calibration_model(
        self,
        filename="calibration_model.json",
        wavelength=None
    ):
        """
        Save calibration parameters for later sample measurements.
        """

        if self.slope is None:
            raise ValueError(
                "Run linear_regression() before saving the calibration model."
            )

        calibration_model = {
            "slope": self.slope,
            "intercept": self.intercept,
            "r2": self.r2,
            "range_min": float(
                self.df["target_concentration"].min()
            ),
            "range_max": float(
                self.df["target_concentration"].max()
            )
        }

        if wavelength is not None:
            calibration_model["wavelength"] = wavelength

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                calibration_model,
                file,
                indent=4
            )

        print(
            f"Calibration model saved to: {filename}"
        )