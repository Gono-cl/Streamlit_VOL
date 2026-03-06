"""Flow and pump calculation helpers for ExperimentRunner."""
from __future__ import annotations


class FlowCalculationsMixin:
    def calculate_flows(self, residence_time, ratio_org_aq, reactor_volume=1.4):
        total_flow = reactor_volume / (residence_time / 60)
        flow_aq = total_flow / (1 + ratio_org_aq)
        flow_org = total_flow - flow_aq
        flow_aq = round(flow_aq, 3)
        flow_org = round(flow_org, 3)
        total_flow = round(total_flow, 3)
        return flow_aq, flow_org, total_flow

    def calculate_flows1(self, residence_time):
        total_flow = 6.4 / (residence_time / 60)
        org_flow = total_flow / 2
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
        value1 = round(value1, 2)
        vorg = round(value1 * 2, 2)
        yes_acid, no_acid = self.calculate_pump_flows(acid, value1)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", vorg)  # flow DCM
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(yes_acid, 2))  # flow TFEA + acid 0.2 M
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(no_acid, 2))  # flow TFEA + acid 0.0 M
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP_4", value1)  # flow NaNO2
        else:
            print("Simulation mode: skipping pump control.")

    def set_pump_flows(self, residence_time):
        total_flow = 11.4 / (residence_time / 60)
        value1 = total_flow / 4
        vorg = round(value1 * 2, 2)

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", vorg)
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(value1, 2))  # NaNO2 1.2 M
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(value1, 2))  # TFEA 1.0 M +  HCl 0.1 M
        else:
            print("Simulation mode: skipping pump control.")

    def set_pump_flows_from_ratio_and_time(self, ratio_org_aq, residence_time, reactor_volume=1.4):
        """
        Control pumps based on ratio_org_aq and residence_time.
        This allows both values to be true variables.
        """
        # Total flow in mL/min
        total_flow = reactor_volume / (residence_time / 60)
        # Calculate flow components
        flow_aq = total_flow / (1 + ratio_org_aq)
        flow_org = (total_flow - flow_aq) * 1000  # values in ul/min instead of ml/min for a better precision
        flow_react1 = flow_aq / 2 * 1000
        flow_react2 = flow_aq / 2 * 1000

        if self.simulation_mode in ["off", "hybrid"]:
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP3.W1", round(flow_react1, 2))  # Reactant 1
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP1.W1", round(flow_react2, 2))  # Reactant 2
            self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3APUMP2.W1", round(flow_org, 2))  # Organic
        else:
            print("Simulation mode: skipping pump control.")
            print(f"-> Organic: {flow_org:.2f} mL/min | React1: {flow_react1:.2f} | React2: {flow_react2:.2f}")

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

    def calculate_residence_time(self, flow_rate, reaction_volume=2):
        """Calculate the residence time based on the flow_rate and reactor+measurement volume."""
        residence_time = reaction_volume / flow_rate * 60  # seconds
        return residence_time * 2  # 1.5 residence times
