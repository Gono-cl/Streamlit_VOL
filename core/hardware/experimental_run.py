import time
import numpy as np
import csv
from core.hardware.opc_communication import OPCClient
from core.objectives import simulate_objectives
import streamlit as st
import matplotlib.pyplot as plt
import os
from datetime import datetime
import numpy as np


class ExperimentRunner:
    def __init__(self, opc_client: OPCClient, csv_filename: str, simulation_mode: str = "off"):
        self.opc = opc_client
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode  # Options: "off", "full", "hybrid"
        self.experiment_status_placeholder = st.sidebar.empty()
        self.countdown_placeholder = st.empty()
        self.timer_placeholder = st.sidebar.empty()
        self.measurements_plot_placeholder = st.empty()
        self.start_time = None
        self.full_measurement_log = []  # Store all measurements for the full experiment

    def initialize_experiment(self, experiment_number, iterations, parameters):
        self.start_time = time.time()
        print(f"🔬 Running Experiment {experiment_number} of {iterations}")
        print(f"🧪 Parameters: {parameters}")
        if self.simulation_mode == "off":
            if not self.opc.check_connection("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1"):
                print("Connection failed. Aborting experiment.")
                return
        self.init_csv()

    def check_water_and_clean_probe(self, threshold=3.0):
        if self.simulation_mode != "off":
            return  print("Cleaning is only run in real hardware mode")
            

        try:
            water_area = self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EWater+-+Area")
            print(f"💧 Water Area = {water_area:.2f}")

            if water_area > threshold:
                print("🧼 Cleaning the optical probe due to high water content...")
                
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_01_CLOSE", 1)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_01_OPEN", 1)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 1)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 1)

                # Start Cleaning
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 1)
                print("🧪 Cleaning with isopropanol...")

                # Cleaning time
                time.sleep(30)

                # Stop Cleaning 
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 0)

                # Switch back valves
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_01_CLOSE", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_01_OPEN", 0)

                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", 1)
                print("🚿 Flushing DCM to remove isopropanol...")
                time.sleep(30)

                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0)

                print("✅ Cleaning complete.")

        except Exception as e:
            print(f"❌ Failed to check water area or perform cleaning: {e}")

    def calculate_pump_flows(self, acid, total_acid):
        yes_acid = (acid / 0.6) * total_acid
        no_acid = total_acid - yes_acid
        return yes_acid, no_acid

    def set_pump_flows_acid(self, acid, residence_time):
        total_flow = 1.4 / (residence_time / 60)
        value1 = total_flow / 4
        Vorg = round(value1 * 2, 2)
        yes_acid, no_acid = self.calculate_pump_flows(acid, total_flow)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", Vorg)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(no_acid, 2))
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(yes_acid, 2))
        else:
            print("🔁 Simulation mode: skipping pump control.")
    
    def set_pump_flows(self, residence_time):
        total_flow = 1.4 / (residence_time / 60)
        value1 = total_flow / 4
        Vorg = round(value1 * 2, 2)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", Vorg)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(value1, 2))
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(value1, 2))
        else:
            print("🔁 Simulation mode: skipping pump control.")

    def set_pressure(self, pressure):
        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APC_OUT", round(pressure, 2))


    def set_pump_flows_from_ratio_and_time(self, ratio_org_aq, residence_time, reactor_volume=1.4):
        """
        Control pumps based on ratio_org_aq and residence_time.
        This allows both values to be true variables.
        """
        # Total flow in mL/min
        total_flow = reactor_volume / (residence_time / 60)

        # Calculate flow components
        flow_aq = total_flow / (1 + ratio_org_aq)
        flow_org = total_flow - flow_aq
        flow_react1 = flow_aq / 2
        flow_react2 = flow_aq / 2

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", round(flow_org, 2))     # Organic
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(flow_react2, 2))  # Reactant 2
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(flow_react1, 2))  # Reactant 1
        else:
            print("🔁 Simulation mode: skipping pump control.")
            print(f"→ Organic: {flow_org:.2f} mL/min | React1: {flow_react1:.2f} | React2: {flow_react2:.2f}")

    def monitor_temperature(self, target_temp):
        
        if self.simulation_mode in ["off", "hybrid"]:
            print("inside the if statement")
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.ON", 1)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.W1", target_temp)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", 0.2) # Organic

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

    def calculate_rsd(self, measurements):
        return (np.std(measurements) / np.mean(measurements)) * 100 if np.mean(measurements) != 0 else float("inf")

    def _read_measurement(self, res_time=None, ratio=None ):
        if self.simulation_mode == "full":
            return np.random.uniform(70, 100)
        elif self.simulation_mode == "hybrid":
            return self.synthetic_raw_area(res_time, ratio)
        else:
            dan_area = float(self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EDAN+-+Area"))
            water_area = float(self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EWater+-+Area"))
            corrected = dan_area + (0.0811122 * water_area)
            normalized = corrected * ratio
            print(f"Corrected measurement (DAN + 0.0811 * Water): {corrected:.2f}")
            print(f"Normalized measurement:{normalized:.2f}")
            return corrected

    def collect_measurements(self, rsd_threshold=2, max_measurements=15, iteration=0, parameters=None):
        measurements = []
        all_measurements = []

        res_time = parameters.get("residence_time", 20)
        ratio = parameters.get("ratio_org_aq", 1.0)

        while len(measurements) < 3:
            val = self._read_measurement(res_time=res_time, ratio=ratio)
            print(f"📏 Measurement {len(measurements)+1} = {val:.2f}")
            measurements.append(val)
            all_measurements.append(val)
            time.sleep(28)

        rsd = self.calculate_rsd(measurements)
        print(f"\n📊 Initial RSD = {rsd:.2f}%")

        while rsd >= rsd_threshold and len(measurements) < max_measurements:
            print("⚠️ RSD too high. Taking another measurement...")
            time.sleep(30)  # Wait before next measurement
            new_val = self._read_measurement(res_time=res_time, ratio=ratio)
            print(f"📏 New Measurement = {new_val:.2f}")
            measurements = measurements[-2:] + [new_val]
            all_measurements.append(new_val)

            rsd = self.calculate_rsd(measurements)
            print(f"📊 Updated RSD = {rsd:.2f}%")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for idx, val in enumerate(all_measurements, 1):
            self.full_measurement_log.append({
                "Iteration": iteration,
                "Timestamp": timestamp,
                **parameters,
                "Measurement #": idx,
                "Value": val
            })

        return np.mean(measurements)

    def stop_pumps(self):
        if self.simulation_mode in ["off", "hybrid"]:
            for pump in ["PUMP1.W1", "PUMP2.W1", "PUMP_4", "PUMP5.W1", "PC_OUT"]:
                self.opc.write_value(f"Hitec_OPC_DA20_Server-%3EDIAZOAN%3A{pump}", 0)
            print("🛑 All pumps stopped.")
        else:
            print("🛑 Simulation mode: skipping pump shutdown.")

    def countdown(self, residence_time):
        for secs in range(residence_time * 9, 0, -1):
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

    def synthetic_raw_area(self, res_time, ratio):
        """
        Generate synthetic raw area based on residence time and ratio_org_aq.
        Shorter residence time and lower ratio yield higher area.
        Output constrained between 3.0 and 4.0.
        """
        base = 4.0 - 0.015 * res_time + 0.3 * (1.5 - ratio) 
        noise = np.random.normal(0, 0.05)
        return float(np.clip(base + noise, 3.0, 4.0))

    def simulate_experiment(self, parameters, objectives=None, directions=None):
        if objectives is None:
            objectives = ["Normalized Area", "Throughput"]

        print("🎲 Simulating experiment...")
        
        reactor_volume = 1.4  # mL
        # Extract required parameters
        res_time = parameters.get("residence_time", 20)
        ratio = parameters.get("ratio_org_aq", 1.0)
        if self.simulation_mode in ["off", "hybrid"]: 
            raw_area = self.collect_measurements(parameters=parameters)
        else:
            raw_area = self.synthetic_raw_area(res_time, ratio)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.full_measurement_log.append({
                "Iteration": parameters.get("iteration", 0),
                "Timestamp": timestamp,
                **parameters,
                "Measurement #": 1,
                "Value": raw_area
            })

        # Calculate flow values
        total_flow = reactor_volume / (res_time / 60)
        flow_aq = total_flow / (1 + ratio)
        flow_org = total_flow - flow_aq

        simulated_result = simulate_objectives(raw_area, flow_aq, flow_org, res_time, selected_objectives=objectives, directions=directions)

        print(f"🧪 Simulated result: {simulated_result}")
        return simulated_result

    def run_experiment(self, parameters, experiment_number=None, total_iterations=None, objectives=None, directions=None):
        if experiment_number is not None and total_iterations is not None:
            print(f"🔬 Running Experiment {experiment_number} of {total_iterations}")
            self.display_experiment_info(experiment_number, total_iterations, parameters)
            
        if self.simulation_mode in ["off", "hybrid"]:
            self.check_water_and_clean_probe()
            self.monitor_temperature(parameters["temperature"])
            self.set_pressure(parameters["pressure"])
            self.set_pump_flows(parameters["residence_time"])
            #self.set_pump_flows_from_ratio_and_time(parameters["ratio_org_aq"], parameters["residence_time"])
            self.countdown(int(parameters["residence_time"]))
        else:
            print("🔁 Full simulation mode enabled: skipping temperature and pump setup.")

        if self.simulation_mode in ["full", "hybrid"]:
            result = self.simulate_experiment(parameters, objectives)
            if len(objectives) == 1:
                result = {objectives[0]: result[objectives[0]]}  # Only return the selected objective

        else:
            mean_measurement = self.collect_measurements(parameters = parameters)
            reactor_volume = 1.4
            res_time = parameters.get("residence_time", 20)
            ratio = parameters.get("ratio_org_aq", 1.0)
            total_flow = reactor_volume /(res_time/60)
            flow_aq = total_flow / (1 + ratio)
            flow_org = total_flow - flow_aq
            
            result = simulate_objectives(
                mean_measurement, flow_aq, flow_org, res_time, selected_objectives=objectives, directions=directions
            )
            
        self.stop_pumps()
        return result

    def save_full_measurements_to_csv(self, experiment_name):
        os.makedirs("raw_measurements", exist_ok=True)
        filename = f"raw_measurements/{experiment_name.replace(' ', '_')}_measurements.csv"
        keys = self.full_measurement_log[0].keys() if self.full_measurement_log else []
        file_exists = os.path.isfile(filename)
        mode = 'a' if file_exists else 'w'
        with open(filename, mode, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.full_measurement_log)
        print(f"📁 Full measurement log saved to {filename}")
        # Clear the log after saving so only new measurements are saved next time
        self.full_measurement_log.clear()
        return filename
















