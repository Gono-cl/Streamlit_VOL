"""Electrochemical control helpers for ExperimentRunner."""
from __future__ import annotations

import time


class EchemMixin:
    def flow_electrochemical_cell(self, flow_rate):
        """Set flow rate for electrochemical reaction, using a single pump."""
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate, 2))  # ul/min

    def flow_electrochemical_cell_dual(self, flow_rate, base_concentration):
        """Set the flow rates from 2 variables like in the case of different amount of Base or Acid."""
        pump1_base = 0  # mM
        pump2_base = 700  # mM

        flow_rate1, flow_rate2 = self.calculate_base_flows(base_concentration, flow_rate, pump1_base, pump2_base)

        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate1 * 1000, 0))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_rate2 * 1000, 0))

    def flow_electrochemical_cell_from_four_variables(self, flow_rate, sub_conc, acid_conc, base_conc):
        """ Set the flow of 4 pumps based in the values of 4 variables
        Variables :
                    flow_rate = total flow rate combined from the 4 pumps
                    sub_conc = concentratiom of substrate in mM
                    acid_conc = concentration of acid in mM
                    base_conc = concentration of base in mM """

        stock_substrate_concentration = 716  # mM
        stock_acid_concentration = 1500  # mM
        stock_base_concentration = 1500  # mM

        # set the automatic valves to reaction position
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        flow_sub = sub_conc * flow_rate / stock_substrate_concentration  # ml/min
        flow_acid = acid_conc * flow_rate / stock_acid_concentration  # ml/min
        flow_base = base_conc * flow_rate / stock_base_concentration  # ml/min
        flow_solvent = flow_rate - (flow_sub + flow_acid + flow_base)  # ml/min

        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_base * 1000, 0))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1", round(flow_acid * 1000, 0))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_solvent * 1000, 0))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1", round(flow_sub * 1000, 0))

    def flow_galvanostatic_mode(self, current, concentration, charge, base_concentration, acid_concentration):
        """Set the flow of pumps for the electrochemical cell in galvanostatic mode."""
        stock_substrate = 2000  # mM
        stock_base = 2000  # mM
        stock_acid = 2000  # mM
        faraday_constant = 96485.33212  # C/mol

        total_flow = (current * 60000 / (concentration * charge * faraday_constant))  # flow rate in ul/min
        flow_substrate = total_flow * (concentration / stock_substrate)
        flow_base = total_flow * (base_concentration / stock_base)
        flow_acid = total_flow * (acid_concentration / stock_acid)
        flow_solvent = total_flow - (flow_substrate + flow_base + flow_acid)

        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_substrate, 2))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP2.W1", round(flow_base, 2))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1", round(flow_acid, 2))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_solvent, 2))

    def set_voltage(self, voltage):
        """Set the voltage for the electrochemical cell."""
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.SETVOLT", round(voltage, 2))

    def set_current(self, current):
        """Set the current for the electrochemical cell."""
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.CURR", round(current, 2))

    def turn_on_power_supply(self):
        """Turn on the power supply for the electrochemical cell."""
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTOFF", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTON", 1)

    def turn_off_power_supply(self):
        """Turn off the power supply for the electrochemical cell."""
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTON", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3ABKK_P.OUTOFF", 1)

    def filling_electrochemical_cell(self, flow_rate, base_concentration, volume=1.8):
        """Fill the electrochemical cell for a specified duration."""
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0)

        pump1_base = 0  # mM
        pump2_base = 700  # mM

        flow_rate1, flow_rate2 = self.calculate_base_flows(base_concentration, flow_rate, pump1_base, pump2_base)
        total_flow = flow_rate1 + flow_rate2
        duration = round(volume / total_flow * 60, 2)

        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP1.W1", round(flow_rate1 * 1000, 2))
        self.opc.write_value("Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1", round(flow_rate2 * 1000, 2))
        print(f"Filling electrochemical cell for {duration} seconds...")
        time.sleep(duration)
