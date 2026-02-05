import time
import numpy as np
import csv
from core.hardware.opc_communication import OPCClient
from core.hardware.autosampler import AutoSampler
from core.objectives import calculate_objectives
import streamlit as st
import matplotlib.pyplot as plt
import os
from datetime import datetime
import numpy as np


class ExperimentRunner:
    def __init__(self, opc_client: OPCClient, csv_filename: str, simulation_mode: str = "off", use_autosampler: bool = False, volume_to_collect: float = 3.0):
        self.opc = opc_client
        self.use_autosampler = use_autosampler
        self.autosampler = AutoSampler(opc_client, vial_volume_ml=2.0) if use_autosampler else None
        self.csv_filename = csv_filename
        self.simulation_mode = simulation_mode  # Options: "off", "full", "hybrid"
        self.experiment_status_placeholder = st.sidebar.empty()
        self.countdown_placeholder = st.empty()
        self.timer_placeholder = st.sidebar.empty()
        self.measurements_plot_placeholder = st.empty()
        self.start_time = None
        self.full_measurement_log = []  # Store all measurements for the full experiment
        self.tray_pos_waste = 0
        self.tray_pos_collect = 1
        self.volume_to_collect = volume_to_collect  # Volume to collect in mL

    
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #                                                       STANDARD PROCESS FUNCTIONS
    #--------------------------------------------------------------------------------------------------------------------------------------------
    # -------------------------------------------------------------------------------------------------------------------------------------------

    def initialize_experiment(self, experiment_number, iterations, parameters):
        self.start_time = time.time()
        print(f"🔬 Running Experiment {experiment_number} of {iterations}")
        print(f"🧪 Parameters: {parameters}")
        if self.simulation_mode == "off":
            if not self.opc.check_connection("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.X1"):
                print("Connection failed. Aborting experiment.")
                return
        self.init_csv()

    def check_water_and_clean_probe(self, threshold=1.0):
        if self.simulation_mode != "off":
            return  print("Cleaning is only run in real hardware mode")
            

        try:
            water_area = self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EWater+-+Area")
            print(f"💧 Water Area = {water_area:.2f}")

            if water_area > threshold:
                print("🧼 Cleaning the optical probe due to high water content...")
                
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 0)
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 1) #check 
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 1) #check

                # Start Cleaning
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 1)
                print("🧪 Cleaning with isopropanol...")

                # Cleaning time
                time.sleep(30)

                # Stop Cleaning 
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_6.W1", 0)

                # Switch back valves
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0) # check 
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0) # check

                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 1)
                print("🚿 Flushing DCM to remove isopropanol...")

                print("✅ Cleaning complete.")

        except Exception as e:
            print(f"❌ Failed to check water area or perform cleaning: {e}")

    def calculate_flows(self, residence_time, ratio_org_aq,reactor_volume=1.4):
        total_flow = reactor_volume / (residence_time / 60)
        flow_aq = total_flow / (1 + ratio_org_aq)
        flow_org = total_flow - flow_aq
        flow_aq = round(flow_aq, 3)
        flow_org = round(flow_org, 3)
        total_flow = round(total_flow, 3)
        return flow_aq, flow_org, total_flow
    
    def calculate_flows1(self, residence_time):
        #total_flow = reactor_volume / (residence_time / 60)
        #flow_aq = total_flow / (1 + ratio_org_aq)
        #flow_org = total_flow - flow_aq
        #flow_aq = round(flow_aq, 3)
        #flow_org = round(flow_org, 3)
        #total_flow = round(total_flow, 3)
        total_flow = 6.4 / (residence_time / 60)
        org_flow = total_flow / 2
        #value1 = total_flow / 3
        #value1 = round(value1,2)
        #Vorg = round(value1, 2)
        #yes_acid, no_acid = self.calculate_pump_flows(acid, value1)
        return org_flow


    def calculate_pump_flows(self, acid, total_acid):
        """
        Compute flow split between two stock solutions to achieve target acid concentration.

        Stocks:
        - Stock A ("yes_acid"): 0.2 M acid
        - Stock B ("no_acid"):  0.0 M acid

        For a desired concentration `acid` in [0.0, 0.2], the fraction from Stock A is
        f = acid / 0.2. The remainder comes from Stock B. Values are clamped to bounds.
        """
        # Clamp target to supported range [0.0, 0.2]
        target = max(0.0, min(float(acid), 0.2))
        # Fraction of the 0.2 M stock required
        frac_strong = target / 0.2 if 0.2 != 0 else 0.0
        # Compute individual flows that sum to total_acid
        yes_acid = total_acid * frac_strong      # 0.2 M stock flow
        no_acid = total_acid - yes_acid          # 0.0 M stock flow
        return yes_acid, no_acid

    def set_pump_flows_acid(self, acid, residence_time):
        total_flow = 6.4 / (residence_time / 60)
        value1 = total_flow / 4
        value1 = round(value1,2)
        Vorg = round(value1 * 2, 2)
        yes_acid, no_acid = self.calculate_pump_flows(acid, value1)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", Vorg) # flow DCM 
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(yes_acid, 2)) # flow TFEA + acid 0.2 M
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(no_acid, 2)) # flow TFEA + acid 0.0 M
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", value1) # flow NaNO2 
        
        else:
            print("🔁 Simulation mode: skipping pump control.")
    
    def set_pump_flows(self, residence_time):
        total_flow = 11.4/ (residence_time / 60)
        value1 = total_flow / 4
        Vorg = round(value1 * 2, 2)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", Vorg)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(value1, 2)) # NaNO2 1.2 M 
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(value1, 2)) # TFEA 1.0 M +  HCl 0.1 M
        else:
            print("🔁 Simulation mode: skipping pump control.")

    def set_pressure(self, pressure):
        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APC_OUT", round(pressure, 2))
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", 2.0) # increase the pressure faster
            time.sleep(30) # wait 30 seconds to reach the desired pressure, this could be improved using the pressure reading to make it more dynamic. 


    def set_pump_flows_from_ratio_and_time(self, ratio_org_aq, residence_time, reactor_volume=1.4):
        """
        Control pumps based on ratio_org_aq and residence_time.
        This allows both values to be true variables.
        """
        # Total flow in mL/min
        total_flow = reactor_volume / (residence_time / 60)
        # Calculate flow components
        flow_aq = total_flow / (1 + ratio_org_aq)
        flow_org = (total_flow - flow_aq) * 1000  #values in ul/min instead of ml/min for a better precision
        flow_react1 = flow_aq / 2 * 1000
        flow_react2 = flow_aq / 2 * 1000

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", round(flow_react1, 2))     # Reactant 1
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(flow_react2, 2))  # Reactant 2
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(flow_org, 2))  # Organic
        else:
            print("🔁 Simulation mode: skipping pump control.")
            print(f"→ Organic: {flow_org:.2f} mL/min | React1: {flow_react1:.2f} | React2: {flow_react2:.2f}")

    def monitor_temperature(self, target_temp):
        
        if self.simulation_mode in ["off", "hybrid"]:
            print("inside the if statement")
            #if reference run is needed run reference to autosampler
            if self.opc.read_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AAUTOSAMPLER.T_STAT") != 2:
                self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AAUTOSAMPLER.REF_T", 1)
            # set valve to waste
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0) # process open
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0) # process open
            print(f"Autosampler: {self.use_autosampler} | Volume to collect: {self.volume_to_collect} ml")
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.ON", 1)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3ACHILLER_01.W1", target_temp)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", 500) # Organic
            print(target_temp)

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

    
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #                                                       REAL TIME MEASUREMENTS FUNCTIONS
    #--------------------------------------------------------------------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------------------------------------------------------

    def calculate_rsd(self, measurements):
        return (np.std(measurements) / np.mean(measurements)) * 100 if np.mean(measurements) != 0 else float("inf")

    def _read_measurement(self):
        if self.simulation_mode == "full":
            return np.random.uniform(70, 100)
        elif self.simulation_mode == "hybrid":
            return np.random.uniform(70, 100)
        else:
            product_area = float(self.opc.read_value("OpusOPCSvr.HP-CZC3484P17->PDA - mM"))
            # water_area = float(self.opc.read_value("OpusOPCSvr.HP-CZC3484P17-%3EWater+-+Area")) # This is OK

            #if water_area > 1.0:
                #product_area = product_area + (0.0913 * water_area) # Corrected area for analyte using water
            
        return product_area

        
    def collect_measurements(self, rsd_threshold=3, max_measurements=15, iteration=0, parameters=None):
        measurements = [] 
        all_measurements = []

        #res_time = parameters.get("residence_time", 20)
        #ratio = parameters.get("ratio_org_aq", 1.0)

        while len(measurements) < 3:
            val = self._read_measurement()
            print(f"📏 Measurement {len(measurements)+1} = {val:.2f}")
            measurements.append(val)
            all_measurements.append(val)
            if len(measurements) < 3:
                time.sleep(20)

        rsd = self.calculate_rsd(measurements)
        print(f"\n📊 Initial RSD = {rsd:.2f}%")

        while rsd >= rsd_threshold and len(measurements) < max_measurements:
            print("⚠️ RSD too high. Taking another measurement...")
            time.sleep(20)  # Wait before next measurement
            new_val = self._read_measurement()
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
            for pump in ["PUMP2.W1", "PUMP1.W1", "PUMP3.W1", "PUMP4.W1", "PC_OUT"]:
                self.opc.write_value(f"Hitec_OPC_DA20_Server->E_CHEM:{pump}", 0) 
            print("🛑 All pumps stopped.")
        else:
            print("🛑 Simulation mode: skipping pump shutdown.")

    def countdown(self, residence_time):
        for secs in range( residence_time * 9, 0, -1):
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

    def synthetic_raw_area(self, res_time):
        """
        Generate synthetic raw area based on residence time and ratio_org_aq.
        Shorter residence time and lower ratio yield higher area.
        Output constrained between 3.0 and 4.0.
        """
        base = 4.0 - 0.015 * res_time + 0.3 * (1.5) 
        noise = np.random.normal(0, 0.05)
        return float(np.clip(base + noise, 3.0, 4.0))

    def simulate_experiment(self, parameters, objectives=None, directions=None):
        if objectives is None:
            objectives = ["Normalized Area", "Throughput"]

        print("🎲 Simulating experiment...")
        
        reactor_volume = 1.4  # mL
        # Extract required parameters
        res_time = parameters.get("Voltage", 20)
        #ratio = parameters.get("ratio_org_aq", 1.0)
        if self.simulation_mode in ["off"]: 
            raw_area = self.collect_measurements(parameters=parameters)
        else:
            raw_area = self.synthetic_raw_area(res_time)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.full_measurement_log.append({
                "Iteration": parameters.get("iteration", 0),
                "Timestamp": timestamp,
                **parameters,
                "Measurement #": 1,
                "Value": raw_area
            })

        # Calculate flow values
        # flow_aq, flow_org, total_flow = self.calculate_flows(parameters["residence_time"], parameters.get("ratio_org_aq", 1.0))
        #total_flow = reactor_volume / (res_time / 60)
        #flow_aq = total_flow / 2
        #flow_org = total_flow - flow_aq

        #simulated_result = simulate_objectives(raw_area, flow_aq, flow_org, res_time, selected_objectives=objectives, directions=directions)

        print(f"🧪 Simulated result: {raw_area}")
        return raw_area
    
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #                                                       ELECTROCHEMICAL FUNCTIONS
    #--------------------------------------------------------------------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------------------------------------------------------
    def flow_electrochemical_cell(self, flow_rate):
        """Set flow rate for electrochemical reaction, using a single pump."""

        #set the value of pump 1 , containing the reaction mixture
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate, 2)) # ul/min

    def flow_electrochemical_cell_dual(self, flow_rate, base_concentration):
        """Set the flow rates from 2 variables like in the case of different amount of Base or Acid."""

        # There are 2 solutions containing the same starting material concentration but different base concentration
        pump1_base = 0 # mM
        pump2_base = 700  # mM

        flow_rate1, flow_rate2 = self.calculate_base_flows(base_concentration, flow_rate, pump1_base, pump2_base)

        # Set the flow rates for both pumps
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate1 * 1000, 0))  # Pump 1 Low Base concentration
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_rate2 * 1000, 0))  # Pump 2 High Base concentration

    def flow_electrochemical_cell_from_four_variables(self, flow_rate, sub_conc, acid_conc, base_conc):
        """ Set the flow of 4 pumps based in the values of 4 variables
        
        Variables :
                    flow_rate = total flow rate combined from the 4 pumps
                    sub_conc = concentratiom of substrate in mM
                    acid_conc = concentration of acid in mM
                    base_conc = concentration of base in mM """
        
        # Define the stock concentrations 

        stock_substrate_concentration = 716 # mM
        stock_acid_concentration = 1500 # mM
        stock_base_concentration = 1500 # mM

         #set the automatic valves to reaction position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1) 
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        # substrate pump 

        flow_sub = sub_conc * flow_rate / stock_substrate_concentration # ml/min

        flow_acid = acid_conc * flow_rate / stock_acid_concentration # ml/min

        flow_base = base_conc * flow_rate / stock_base_concentration # ml/min

        flow_solvent = flow_rate - (flow_sub + flow_acid + flow_base) # ml/min

        # Set the flow rates for both pumps
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_base * 1000, 0))  # Pump 1 Base flow
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1", round(flow_acid * 1000, 0))  # Pump 2 Acid flow
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_solvent * 1000, 0))  # Pump 3 ACN
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1", round(flow_sub * 1000, 0))  # Pump 4 flow substrate


    
    def flow_galvanostatic_mode(self, current, concentration, charge, base_concentration, acid_concentration):
        """Set the flow of pumps for the electrochemical cell in galvanostatic mode."""
        
        # There are 4 syringes pumps containing 1- Starting material , 2a- base, 2b- acid , 3- solvent
        
        stock_substrate = 2000 # mM
        stock_base = 2000 # mM
        stock_acid = 2000 # mM
        Faraday_constant = 96485.33212 # C/mol


        total_flow = (current * 60000 / (concentration * charge *Faraday_constant)) # flow rate in ul/min
        flow_substrate = total_flow * (concentration / stock_substrate)
        flow_base = total_flow * (base_concentration / stock_base)
        flow_acid = total_flow * (acid_concentration / stock_acid)
        flow_solvent = total_flow - (flow_substrate + flow_base + flow_acid)

        # Set the flow rates for all pumps
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_substrate, 2))  # Pump 1 Starting material
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1", round(flow_base, 2))  # Pump 2 Base
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1", round(flow_acid, 2))  # Pump 4 Acid
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_solvent, 2))  # Pump 3 Solvent


    def calculate_base_flows(self, target_base_conc, total_flow, pump1_base, pump2_base):
        """
        Compute flow split between two stock solutions to achieve target base concentration.

        Stocks:
        - Stock A ("pump1_base"): e.g., 0.0 M base
        - Stock B ("pump2_base"): e.g., 2.0 M base

        For a desired concentration `target_base_conc`, the fraction from Stock B is
        f = (target_base_conc - pump1_base) / (pump2_base - pump1_base). The remainder comes from Stock A. Values are clamped to bounds.
        """
        # Clamp target to supported range
        target = max(min(float(target_base_conc), pump2_base), pump1_base)
        # Fraction of the higher concentration stock required
        frac_strong = (target - pump1_base) / (pump2_base - pump1_base) if (pump2_base - pump1_base) != 0 else 0.0
        # Compute individual flows that sum to total_flow
        flow_pump2 = total_flow * frac_strong      # Higher concentration stock flow
        flow_pump1 = total_flow - flow_pump2       # Lower concentration stock flow
        return flow_pump1, flow_pump2


    def set_voltage (self, voltage): 
        """Set the voltage for the electrochemical cell."""
    
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.SETVOLT", round(voltage, 2)) # Set the voltage value in the power supply

    def set_current (self, current):
        """Set the current for the electrochemical cell."""

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.CURR", round(current, 2)) # Set the current value in the power supply
    
    def turn_on_power_supply(self):
        """Turn on the power supply for the electrochemical cell."""

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTOFF", 0) # turn off the electrochemical cell
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTON", 1) # turn on the electrochemical cell
    
    def turn_off_power_supply(self):
        """Turn off the power supply for the electrochemical cell."""

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTON", 0) # turn off the electrochemical cell
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTOFF", 1) # turn off the electrochemical cell
    
    def filling_electrochemical_cell(self,flow_rate, base_concentration, volume = 1.8):
        """Fill the electrochemical cell for a specified duration."""

        #set the automatic valves to reaction position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1) 
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        # There are 2 solutions containing the same starting material concentration but different base concentration
        pump1_base = 0 # mM
        pump2_base = 700  # mM

        flow_rate1, flow_rate2 = self.calculate_base_flows(base_concentration, flow_rate, pump1_base, pump2_base)
        total_flow = flow_rate1+flow_rate2
        duration = round(volume/total_flow * 60,2)

        # Set the flow rates for both pumps
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate1 * 1000, 2))  # Pump 1 Low Base concentration
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_rate2 * 1000, 2))  # Pump 2 High Base concentration
        print(f"Filling electrochemical cell for {duration} seconds...")
        time.sleep(duration) # change for duration
    
    def calculate_residence_time(self, flow_rate, reaction_volume = 2):
        """ Calculate the residence time based in the flow_rate and the reaction and measure volume
            Measure volume include all the death volumes downstream"""
        
        residence_time = reaction_volume/flow_rate *60 # calculate the residence time in seconds
        
        return residence_time * 1.5 # 1.5 residence times
    
    def countdown_echem(self, flow_rate):

        residence_time = self.calculate_residence_time(flow_rate)
        residence_time = int(residence_time)
        for secs in range( residence_time, 0, -1): # change for residence time
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>⏳ Countdown to Reach Steady State</h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)

    def time_for_measurements(self, flow_rate):

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 1)
        print("Valve switched to measurement position.")
        
        downstream_vol = 1.0 # separator + measurement cell
        time_until_collection = (downstream_vol/flow_rate) * 60
        time_until_collection = int(time_until_collection)

        for secs in range( time_until_collection, 0, -1): # change for residence time
            mm, ss = secs // 60, secs % 60
            countdown_html = f"""
            <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
                <h4 style='margin:0;'>⏳ Countdown to collect measurements </h4>
                <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
            </div>
            """
            self.countdown_placeholder.markdown(countdown_html, unsafe_allow_html=True)
            time.sleep(1)
    
    def cleaning_electrochemical_cell(self):
        """Clean the electrochemical cell by flushing with different solvent."""

        print("Starting cleaning of electrochemical cell...")
        time.sleep(1)

        # ACN Cell Cleaning 
        #set the automatic valves to cleaning position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0)
        print("Valves switched to cleaning position.")
                
        # rotary valve position for first cleaning solvent
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 3) 

        # start the cleaning pump 
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2) # peristaltic pump with a flow of 3 ml/min
        print("Cleaning electrochemical cell with solvent 1...")    
        time.sleep(60)  # cleaning time 1 minute
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0) # stop the cleaning pump
        print("First cleaning step complete.")

        time.sleep(5)

        # Water Probe Cleaning 

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 1)

        # rotary valve position for second cleaning solvent
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 1) # Water position
        # start the cleaning pump
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2) # peristaltic pump with a flow of 2 ml/min
        print("Cleaning optical probe with water...")    
        time.sleep(60)  # cleaning time 1 minute
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0) # stop the cleaning pump
        print("Second cleaning step complete.")

        time.sleep(5) # delay to ensure pump is fully stopped

        
        # Isopropanol Probe Cleaning 

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 5) # Isopropanol position
        # start the cleaning pump
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2) # peristaltic pump with a flow of 2 ml/min
        print("Cleaning optical probe with Isopropanol...")    
        time.sleep(60)  # cleaning time 1 minute
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0) # stop the cleaning pump
        print("Third cleaning step complete.")

        time.sleep(5) # delay to ensure pump is fully stopped


        # TFA Probe Cleaning 

        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        # rotary valve position for second cleaning solvent
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AROT_VALVE.POS", 4) # TFA position
        # start the cleaning pump
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 2) # peristaltic pump with a flow of 2 ml/min
        print("Cleaning electrochemical cell with TFA 10%...")    
        time.sleep(60)  # cleaning time 1 minute
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3APUMP_6.W1", 0) # stop the cleaning pump
        print("Last cleaning step complete.")

        time.sleep(5) # delay to ensure pump is fully stopped


        #set the automatic valves to reaction position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1) 
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        print("Valves switched back to reaction position.")
        time.sleep(1)
        print("✅ Cleaning of electrochemical cell complete.")


    #--------------------------------------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #                                                       RUNNING EXPERIMENT FUNCTIONS
    #--------------------------------------------------------------------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------------------------------------------------------


    def run_experiment(self, parameters, experiment_number=None, total_iterations=None, objectives=None, directions=None):
        if experiment_number is not None and total_iterations is not None:
            print(f"🔬 Running Experiment {experiment_number} of {total_iterations}")
            self.display_experiment_info(experiment_number, total_iterations, parameters)
            
        if self.simulation_mode in ["off", "hybrid"]:

            # Diazo in continuous flow set_up

            #self.check_water_and_clean_probe()
            #self.monitor_temperature(parameters["temperature"])
            #self.set_pressure(parameters["pressure"])
            #self.set_pump_flows(parameters["residence_time"])
            #self.set_pump_flows_acid( parameters["acid"], parameters["residence_time"])
            #self.countdown(int(parameters["residence_time"]))

            # Electrochemical continuous flow set_up
            self.flow_electrochemical_cell_from_four_variables(parameters["flow_rate"], parameters["substrate_concentration"], parameters["acid_concentration"], parameters["base_concentration"])
            filling_time = round(0.8/ parameters["flow_rate"] * 1.5 * 60, 2) # time in seconds
            print(f"filling the electrochemical cell for {filling_time} seconds ")
            time.sleep(filling_time) # time required to fill de electrochemical cell (0.8 ml of internal volume), 1.5 is to be sure the cell is filled
            self.set_voltage(parameters["Voltage"])
            self.turn_on_power_supply()
            self.countdown_echem(parameters["flow_rate"])
            
           

        else:
            print("🔁 Full simulation mode enabled: skipping temperature and pump setup.")

        if self.simulation_mode in ["full", "hybrid"]:
            result = self.simulate_experiment(parameters, objectives)
            flow_aq, flow_org, total_flow = self.calculate_flows(parameters["residence_time"], parameters.get("ratio_org_aq", 1.0))
            reactor_volume = 1.4
            res_time = parameters.get("residence_time", 20)
            total_flow = reactor_volume /(res_time/60)
            flow_aq = total_flow / 2
            flow_org = total_flow - flow_aq
            result = calculate_objectives(result, flow_aq, flow_org, res_time, selected_objectives=objectives, directions=directions)
            if len(objectives) == 1:
                result = {objectives[0]: result}  # Only return the selected objective

        else:
            mean_measurement = self.collect_measurements(parameters = parameters)
            #flow_aq, flow_org, total_flow = self.calculate_flows(parameters["residence_time"], parameters.get("ratio_org_aq", 1.0))
            #res_time = parameters.get("residence_time", 20)
            #print(f'mean_measurement : {mean_measurement}')
            #print(f'flow_aq:{flow_aq}')
            #print(f'flow_org : {flow_org}')
            #print(f'residence_time : {res_time}')
            result = calculate_objectives(mean_measurement, parameters["substrate_concentration"], selected_objectives=objectives, directions=directions)

        if self.use_autosampler:
            self.autosampler.clean_before_collect(self.tray_pos_waste)
            self.autosampler.move_prepare_needle(self.tray_pos_collect)
            flow_org = self.calculate_flows1(parameters["residence_time"])
            self.autosampler.start_collection(flow_rate=flow_org, volume=self.volume_to_collect)  # Collect desired volume
            self.tray_pos_waste = (self.tray_pos_waste + 2) % 32
            self.tray_pos_collect = (self.tray_pos_collect + 2) % 32
        else:
            print("ℹ️ Autosampler disabled: skipping sample collection.")

        self.turn_off_power_supply()
        self.stop_pumps()
        self.cleaning_electrochemical_cell()
        return result
    

    #--------------------------------------------------------------------------------------------------------------------------------------------
    #--------------------------------------------------------------------------------------------------------------------------------------------
    #                                                       SAVING FUNCTIONS
    #--------------------------------------------------------------------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------------------------------------------------------

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

    













