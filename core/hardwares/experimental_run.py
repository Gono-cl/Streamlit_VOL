import time
import numpy as np
import csv
from core.hardwares.opc_communication import OPCClient

class ExperimentRunner:
    def __init__(self, opc_client, csv_filename, simulation_mode=False):
        self.opc = opc_client
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode

    def initialize_experiment(self, experiment_number, iterations, parameters):
        if not self.opc.check_connection("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1"):
            print("Connection failed. Aborting experiment.")
            return
        print(f"\nStarting Experiment {experiment_number} of {iterations}")
        print(f"\nParameters: {parameters}")
        self.init_csv()

    def set_pump_flows(self, acid, residence_time):
        total_flow = 1.4 / (residence_time / 60)
        value1 = total_flow / 4
        Vorg = round(value1 * 2, 2)
        yes_acid, no_acid = self.calculate_pump_flows(acid, total_flow)

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_3", 0.2)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(no_acid, 2))
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP5.W1", round(yes_acid, 2))

    def calculate_pump_flows(self, acid_percent, total_flow):
        yes_acid = acid_percent * total_flow
        no_acid = (1 - acid_percent) * total_flow
        return yes_acid, no_acid

    def monitor_temperature(self, target_temp):
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.ON", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.W1", target_temp)

        while True:
            current_temp = self.opc.read_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1")
            if current_temp is None:
                print("Warning: Failed to read temperature from OPC")
                time.sleep(5)
                continue
            if abs(current_temp - target_temp) <= 0.5:
                print(f"Target temperature reached: {target_temp}°C")
                break
            time.sleep(5)

    def collect_measurements(self, residence_time):
        measurements = []
        for i in range(5):
            if self.simulation_mode:
                DAN_Area = np.random.uniform(3, 3.1)
            else:
                DAN_Area = self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EDAN+-+Area")

            measurement = float(DAN_Area)
            print(f"\nMeasurement {i + 1} = {measurement}")
            measurements.append(measurement)

            with open(self.csv_filename, 'a', newline='') as csvfile:
                csvwriter = csv.writer(csvfile)
                csvwriter.writerow([measurement])

            if i < 4:
                time.sleep(28)

        return np.mean(measurements)

    def stop_pumps(self):
        for pump in ["PUMP1.W1", "PUMP2.W1", "PUMP_3", "PUMP5.W1", "PC_OUT"]:
            self.opc.write_value(f"Hitec_OPC_DA20_Server-%3EDIAZOAN%3A{pump}", 0)
        print("All pumps stopped.")

    def init_csv(self):
        with open(self.csv_filename, 'w', newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(["Measurement"])

    def run_experiment(self, parameters):
        self.monitor_temperature(parameters["temperature"])
        self.set_pump_flows(parameters["acid"], parameters["residence_time"])
        mean_measurement = self.collect_measurements(parameters["residence_time"])
        self.stop_pumps()

        return {"Yield": mean_measurement}


