import time
import numpy as np
import csv
from core.hardware.opc_communication import OPCClient
import streamlit as st

class ExperimentRunner:
    def __init__(self, opc_client: OPCClient, csv_filename: str, simulation_mode: str = "off"):
        self.opc = opc_client
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode  # Options: "off", "full", "hybrid"
        self.experiment_status_placeholder = st.sidebar.empty()
        self.countdown_placeholder = st.empty()
        self.timer_placeholder = st.sidebar.empty()
        self.start_time = None

    def initialize_experiment(self, experiment_number, iterations, parameters):
        self.start_time = time.time()
        print(f"🔬 Running Experiment {experiment_number} of {iterations}")
        print(f"🧪 Parameters: {parameters}")
        if self.simulation_mode == "off":
            if not self.opc.check_connection("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1"):
                print("Connection failed. Aborting experiment.")
                return
        self.init_csv()

    def calculate_pump_flows(self, acid, total_acid):
        yes_acid = (acid / 0.6) * total_acid
        no_acid = total_acid - yes_acid
        return yes_acid, no_acid

    def set_pump_flows(self, acid, residence_time):
        total_flow = 1.4 / (residence_time / 60)
        value1 = total_flow / 4
        Vorg = round(value1 * 2, 2)
        yes_acid, no_acid = self.calculate_pump_flows(acid, total_flow)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_3", Vorg)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(no_acid, 2))
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(yes_acid, 2))
        else:
            print("🔁 Simulation mode: skipping pump control.")

    def set_pressure(self, pressure):
        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APC_OUT", round(pressure, 2))

    def monitor_temperature(self, target_temp):
        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.ON", 1)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.W1", target_temp)

            print(f"🧊 Waiting for temperature to reach {target_temp}°C...")

            while True:
                current_temp = self.opc.read_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1")
                print(f"🌡️ Current temperature reading: {current_temp}")

                try:
                    current_temp = float(current_temp)
                except (TypeError, ValueError):
                    print("⚠️ Invalid temperature reading. Retrying...")
                    time.sleep(3)
                    continue

                diff = abs(current_temp - target_temp)
                print(f"📉 ΔT = {diff:.2f}°C")

                if diff <= 0.5:
                    print(f"✅ Target temperature reached: {current_temp:.2f}°C")
                    break

                time.sleep(5)
        else:
            print("🌡️ Simulation mode: skipping temperature control.")

    def collect_measurements(self):
        measurements = []
        for i in range(5):
            if self.simulation_mode in ["full", "hybrid"]:
                DAN_Area = np.random.uniform(0, 3)
            else:
                DAN_Area = self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EDAN+-+Area")

            measurement = float(DAN_Area)
            print(f"📏 Measurement {i + 1} = {measurement:.2f}")
            measurements.append(measurement)

            with open(self.csv_filename, 'a', newline='') as csvfile:
                csvwriter = csv.writer(csvfile)
                csvwriter.writerow([measurement])

            if i < 4:
                time.sleep(2)

        return np.mean(measurements)

    def stop_pumps(self):
        if self.simulation_mode in ["off", "hybrid"]:
            for pump in ["PUMP1.W1", "PUMP2.W1", "PUMP_3", "PUMP5.W1", "PC_OUT"]:
                self.opc.write_value(f"Hitec_OPC_DA20_Server-%3EDIAZOAN%3A{pump}", 0)
            print("🛑 All pumps stopped.")
        else:
            print("🛑 Simulation mode: skipping pump shutdown.")

    def countdown(self, residence_time):
        for secs in range(residence_time * 3, 0, -1):
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>⏳ Countdown to Reach Steady State</h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)

    def display_experiment_info(self, experiment_number, total_iterations, parameters):
        elapsed = time.time() - self.start_time if self.start_time else 0
        mins, secs = divmod(int(elapsed), 60)

        # Update sidebar timer
        self.timer_placeholder.markdown(f"⏱️ **Total Time Running:** {mins:02d}:{secs:02d}")

        html = f"""
        <div style='background-color:#eef6fb; padding: 10px; border-left: 5px solid #2c91c6;'>
            <h4 style='margin:0;'>🔎 Experiment {experiment_number} of {total_iterations}</h4>
            <p style='margin:5px 0 10px 0;'>⏱️ Elapsed Time: {mins:02d}:{secs:02d}</p>
            <ul style='padding-left: 20px;'>
        """
        for key, val in parameters.items():
            html += f"<li><strong>{key}</strong>: {val:.2f}</li>"
        html += "</ul></div>"
        self.experiment_status_placeholder.markdown(html, unsafe_allow_html=True)

    def simulate_experiment(self, parameters, objectives=None):
        if objectives is None:
            objectives = ["Yield"]
        simulated_result = {}

        for obj in objectives:
            if obj == "Yield":
                score = np.exp(-((parameters.get("acid", 0.5) - 0.3) ** 2) * 10) * \
                        np.exp(-((parameters.get("temperature", 50) - 40) ** 2) / 100)
            elif obj == "Conversion":
                score = np.exp(-((parameters.get("residence_time", 1.0) - 3.0) ** 2)) + np.random.uniform(0, 0.1)
            elif obj == "Transformation":
                score = np.random.uniform(0.5, 1.0)
            elif obj == "Productivity":
                score = (parameters.get("pressure", 1.0) / 10) * np.random.uniform(0.7, 1.0)
            else:
                score = np.random.uniform(0, 1)

            simulated_result[obj] = round(score * 100, 2)

        print(f"🧪 Simulated result: {simulated_result}")
        return simulated_result

    def run_experiment(self, parameters, experiment_number=None, total_iterations=None, objectives=None):
        if experiment_number is not None and total_iterations is not None:
            print(f"🔬 Running Experiment {experiment_number} of {total_iterations}")
            self.display_experiment_info(experiment_number, total_iterations, parameters)

        if self.simulation_mode in ["off", "hybrid"]:
            self.monitor_temperature(parameters["temperature"])
            self.set_pressure(parameters["pressure"])
            self.set_pump_flows(parameters["acid"], parameters["residence_time"])
            self.countdown(int(parameters["residence_time"]))
        else:
            print("🔁 Full simulation mode enabled: skipping temperature and pump setup.")

        if self.simulation_mode in ["full", "hybrid"]:
            result = self.simulate_experiment(parameters, objectives)
        else:
            mean_measurement = self.collect_measurements()
            result = {"Yield": mean_measurement}

        self.stop_pumps()
        return result













