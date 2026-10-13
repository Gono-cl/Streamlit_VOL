from pathlib import Path
import time

import numpy as np
import pandas as pd


class SpectragryphReader:

    def __init__(
        self,
        folder,
        file_pattern="my_acquired_spectra_*.csv"
    ):
        self.folder = Path(folder)
        self.file_pattern = file_pattern

    def get_latest_file(self):
        """
        Find the most recently created/modified
        Spectragryph spectrum file.
        """

        files = list(
            self.folder.glob(self.file_pattern)
        )

        if not files:
            raise FileNotFoundError(
                f"No spectrum files found in {self.folder}"
            )

        latest_file = max(
            files,
            key=lambda file: file.stat().st_mtime
        )

        return latest_file

    def wait_until_file_ready(
        self,
        file_path,
        check_interval=0.2
    ):
        """
        Make sure Spectragryph has finished writing
        the file before Python reads it.
        """

        previous_size = -1

        while True:

            current_size = file_path.stat().st_size

            if current_size == previous_size:
                break

            previous_size = current_size

            time.sleep(check_interval)

    def read_spectrum(self, file_path):
        """
        Read a Spectragryph CSV file.

        Row 1 contains spectrum information.
        Row 2 contains:
        Wavelength [nm], Absorbance
        """

        df = pd.read_csv(
            file_path,
            skiprows=1,
            usecols=[0, 1]
        )

        df.columns = [
            "wavelength",
            "absorbance"
        ]

        wavelength = (
            df["wavelength"]
            .to_numpy(dtype=float)
        )

        absorbance = (
            df["absorbance"]
            .to_numpy(dtype=float)
        )

        return wavelength, absorbance

    def get_absorbance(
        self,
        file_path,
        target_wavelength=664.0
    ):
        """
        Extract absorbance at the detector wavelength
        closest to the requested wavelength.
        """

        wavelength, absorbance = \
            self.read_spectrum(file_path)

        index = np.argmin(
            np.abs(
                wavelength
                - target_wavelength
            )
        )

        measured_wavelength = wavelength[index]

        measured_absorbance = absorbance[index]

        return (
            measured_wavelength,
            measured_absorbance
        )

    def get_latest_absorbance(
        self,
        target_wavelength=664.0
    ):
        """
        Find the newest Spectragryph file and
        extract absorbance from it.
        """

        latest_file = self.get_latest_file()

        self.wait_until_file_ready(
            latest_file
        )

        measured_wavelength, absorbance = \
            self.get_absorbance(
                latest_file,
                target_wavelength
            )

        return (
            latest_file,
            measured_wavelength,
            absorbance
        )

    def get_band_average(
        self,
        file_path,
        center=664.0,
        half_width=2.0
    ):
        """
        Optional:
        calculate mean absorbance over a small
        wavelength interval.
        """

        wavelength, absorbance = \
            self.read_spectrum(file_path)

        mask = (
            (wavelength >= center - half_width)
            &
            (wavelength <= center + half_width)
        )

        if not np.any(mask):
            raise ValueError(
                "No wavelength points found "
                "inside the selected range."
            )

        average_absorbance = \
            absorbance[mask].mean()

        return average_absorbance