"""Cleaning and maintenance helpers for ExperimentRunner."""
from __future__ import annotations

import time


class MaintenanceMixin:
    def check_water_and_clean_probe(self, threshold=1.0):
        if self.simulation_mode != "off":
            print("Cleaning is only run in real hardware mode")
            return

        try:
            water_area = self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EWater+-+Area")
            print(f"Water Area = {water_area:.2f}")

            if water_area > threshold:
                print("Cleaning the optical probe due to high water content...")

                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 1)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 1)

                # Start Cleaning
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 1)
                print("Cleaning with isopropanol...")
                time.sleep(30)

                # Stop Cleaning
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 0)

                # Switch back valves
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0)

                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 1)
                print("Flushing DCM to remove isopropanol...")
                print("Cleaning complete.")

        except Exception as e:
            print(f"Failed to check water area or perform cleaning: {e}")

    def monitor_temperature(self, target_temp):
        if self.simulation_mode in ["off", "hybrid"]:
            if self.opc.read_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AAUTOSAMPLER.T_STAT") != 2:
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AAUTOSAMPLER.REF_T", 1)
            # set valve to waste
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0)
            print(f"Autosampler: {self.use_autosampler} | Volume to collect: {self.volume_to_collect} ml")
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.ON", 1)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.W1", target_temp)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", 500)
            print(f"Waiting for temperature to reach {target_temp} C...")

            while True:
                current_temp = self.opc.read_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1")
                print(f"Current temperature reading: {current_temp}")

                try:
                    current_temp = float(current_temp)
                except (TypeError, ValueError):
                    print("Invalid temperature reading. Retrying...")
                    time.sleep(3)
                    continue

                diff = abs(current_temp - target_temp)
                print(f"Delta T = {diff:.2f} C")

                if diff <= 0.5:
                    print(f"Target temperature reached: {current_temp:.2f} C")
                    break
                time.sleep(5)
        else:
            print("Simulation mode: skipping temperature control.")

    def stop_pumps(self):
        if self.simulation_mode in ["off", "hybrid"]:
            for pump in ["PUMP2.W1", "PUMP1.W1", "PUMP3.W1", "PUMP4.W1", "PC_OUT"]:
                self.opc.write_value(f"Hitec_OPC_DA20_Server->E_CHEM:{pump}", 0)
            print("All pumps stopped.")
        else:
            print("Simulation mode: skipping pump shutdown.")

    def cleaning_electrochemical_cell(self):
        """Clean the electrochemical cell by flushing with different solvent."""
        if self.simulation_mode not in ["off", "hybrid"]:
            print("Simulation mode: skipping electrochemical-cell cleaning.")
            return
        print("Starting cleaning of electrochemical cell...")
        time.sleep(1)

        # ACN Cell Cleaning
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0)
        print("Valves switched to cleaning position.")
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 3)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2)
        print("Cleaning electrochemical cell with solvent 1...")
        time.sleep(60)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
        print("First cleaning step complete.")

        time.sleep(5)

        # Water Probe Cleaning
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2)
        print("Cleaning optical probe with water...")
        time.sleep(60)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
        print("Second cleaning step complete.")

        time.sleep(5)

        # Isopropanol Probe Cleaning
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 5)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2)
        print("Cleaning optical probe with Isopropanol...")
        time.sleep(60)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
        print("Third cleaning step complete.")

        time.sleep(5)

        # TFA Probe Cleaning
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 4)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2)
        print("Cleaning electrochemical cell with TFA 10%...")
        time.sleep(60)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0)
        print("Last cleaning step complete.")

        time.sleep(5)

        # set the automatic valves to reaction position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        print("Valves switched back to reaction position.")
        time.sleep(1)
        print("Cleaning of electrochemical cell complete.")

    def set_pressure(self, pressure):
        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APC_OUT", round(pressure, 2))
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 2.0)
            time.sleep(30)
        else:
            print("Simulation mode: skipping pressure setup.")
