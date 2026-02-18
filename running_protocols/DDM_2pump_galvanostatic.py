"""
Running protocol: 2-pump galvanostatic flow electrosynthesis (P3 + P4).

Variables (optimization inputs):
- substrate_concentration [mM]        target concentration in reactor stream
- applied_charge [F/mol]
- current_density [mA/cm2]

Computed:
- total_current_mA = current_density * electrode_area_cm2
- total_flow_ml_min = (total_current_mA / 1000 / F / applied_charge) * 60 / substrate_concentration * 10e6
- flow_pump4_ml_min = (substrate_concentration / stock_substrate_concentration) * total_flow_ml_min
- flow_pump3_ml_min = total_flow_ml_min - flow_pump4_ml_min
- residence_time_to_measurement_s = (total_volume_to_measurement_ml / total_flow_ml_min) * 60
"""

from __future__ import annotations

import time

from core.objectives import calculate_objectives


FARADAY_CONSTANT = 96485.33212  # C/mol
ELECTRODE_AREA_CM2 = 17.25
STOCK_SUBSTRATE_CONCENTRATION_MM = 300.0  # mM in pump 4 stock
ECHEM_CELL_VOLUME_ML = 0.8
TOTAL_VOLUME_TO_MEASUREMENT_ML = 2.8  # 0.8 mL cell + 2x0.5 mL separators + 1.0 mL measurement cell
DOWNSTREAM_VOLUME_TO_MEASUREMENT_ML = TOTAL_VOLUME_TO_MEASUREMENT_ML - ECHEM_CELL_VOLUME_ML
RESIDENCE_TIME_FACTOR = 1.0  # Set >1.0 if you want extra stabilization margin

VALVE_REACTION_TAGS = (
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_CLOSE", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_02_OPEN", 1),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_CLOSE", 0),
    ("Hitec_OPC_DA20_Server-%3EE_CHEM%3AV_01_OPEN", 0),
)

PUMP3_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP3.W1"  # ACN
PUMP4_TAG = "Hitec_OPC_DA20_Server->E_CHEM:PUMP4.W1"  # Substrate stock (300 mM)


def protocol_info():
    return {
        "title": "DDM 2-Pump Galvanostatic (P3/P4)",
        "description": (
            "Protocol for a 2-pump electrosynthesis setup using galvanostatic control. "
            "Pump 4 delivers substrate stock (300 mM), pump 3 delivers ACN solvent. "
            "Total current and flow are computed from current density, applied charge, "
            "and target substrate concentration."
        ),
        "measurement_source_prefix": "OpusOPCSvr.HP-CZC3484P17->",
        "measurement_source_signal": "PDA - mM",
    }


def required_parameter_keys():
    return [
        "substrate_concentration",
        "applied_charge",
        "current_density",
    ]


def _compute_operating_point(parameters):
    substrate_target_mM = float(parameters["substrate_concentration"])
    applied_charge = float(parameters["applied_charge"])
    current_density_mA_cm2 = float(parameters["current_density"])

    if substrate_target_mM <= 0:
        raise ValueError("substrate_concentration must be > 0 mM.")
    if applied_charge <= 0:
        raise ValueError("applied_charge must be > 0 F/mol.")
    if current_density_mA_cm2 <= 0:
        raise ValueError("current_density must be > 0 mA/cm2.")

    total_current_mA = current_density_mA_cm2 * ELECTRODE_AREA_CM2
    total_current_A = total_current_mA / 1000.0

    # Total flow in mL/min with substrate concentration in mM.
    # Q[mL/min] = I[A] / (F * charge[F/mol]) * 60[s/min] * 1e6 / C[mM]
    total_flow_ml_min = (
        (total_current_mA / 1000.0 / FARADAY_CONSTANT / applied_charge)
        * 60.0
        / substrate_target_mM
        * 1e6
    )

    flow_pump4_ml_min = (substrate_target_mM / STOCK_SUBSTRATE_CONCENTRATION_MM) * total_flow_ml_min
    flow_pump3_ml_min = total_flow_ml_min - flow_pump4_ml_min

    if total_flow_ml_min <= 0:
        raise ValueError("Computed total flow is <= 0 mL/min. Check inputs.")
    if flow_pump4_ml_min < 0:
        raise ValueError("Computed pump 4 flow is negative. Check inputs.")
    if flow_pump3_ml_min < 0:
        raise ValueError("Computed pump 3 flow is negative. Target concentration cannot exceed stock constraints.")

    if DOWNSTREAM_VOLUME_TO_MEASUREMENT_ML < 0:
        raise ValueError("DOWNSTREAM_VOLUME_TO_MEASUREMENT_ML cannot be negative.")

    filling_time_cell_s = (ECHEM_CELL_VOLUME_ML / total_flow_ml_min) * 60.0 *1.5  # Add margin to ensure cell is fully filled before powering on.
    residence_time_s = (TOTAL_VOLUME_TO_MEASUREMENT_ML / total_flow_ml_min) * 60.0 * RESIDENCE_TIME_FACTOR
    measurement_time_after_power_s = (
        DOWNSTREAM_VOLUME_TO_MEASUREMENT_ML / total_flow_ml_min
    ) * 60.0 * RESIDENCE_TIME_FACTOR

    return {
        "substrate_target_mM": substrate_target_mM,
        "applied_charge": applied_charge,
        "current_density_mA_cm2": current_density_mA_cm2,
        "total_current_mA": total_current_mA,
        "total_current_A": total_current_A,
        "total_flow_ml_min": total_flow_ml_min,
        "flow_pump4_ml_min": flow_pump4_ml_min,
        "flow_pump3_ml_min": flow_pump3_ml_min,
        "filling_time_cell_s": filling_time_cell_s,
        "measurement_time_after_power_s": measurement_time_after_power_s,
        "residence_time_to_measurement_s": residence_time_s,
    }


def _countdown_to_measurement(runner, seconds):
    secs_total = max(0, int(round(float(seconds))))
    if secs_total <= 0:
        return
    for secs in range(secs_total, 0, -1):
        mm, ss = divmod(secs, 60)
        html = f"""
        <div style='background-color:#fff3cd; padding: 15px; border-left: 5px solid #ffca28; border-radius: 5px;'>
            <h4 style='margin:0;'>Countdown to collect measurements</h4>
            <p style='font-size: 24px; font-weight: bold; color: #856404; margin: 5px 0 0 0;'>{mm:02d}:{ss:02d}</p>
        </div>
        """
        runner.countdown_placeholder.markdown(html, unsafe_allow_html=True)
        time.sleep(1)


def prepare_hardware(runner, parameters):
    op = _compute_operating_point(parameters)

    print("2-pump galvanostatic protocol:")
    print(f"  current density = {op['current_density_mA_cm2']:.3f} mA/cm2")
    print(f"  total current   = {op['total_current_mA']:.3f} mA ({op['total_current_A']:.5f} A)")
    print(f"  applied charge  = {op['applied_charge']:.3f} F/mol")
    print(f"  target [Sub]    = {op['substrate_target_mM']:.3f} mM")
    print(f"  total flow      = {op['total_flow_ml_min']:.4f} mL/min")
    print(f"  pump4 (stock)   = {op['flow_pump4_ml_min']:.4f} mL/min")
    print(f"  pump3 (ACN)     = {op['flow_pump3_ml_min']:.4f} mL/min")
    print(f"  t_fill_cell     = {op['filling_time_cell_s']:.1f} s")
    print(f"  t_measurement   = {op['residence_time_to_measurement_s']:.1f} s (from flow change)")
    print(f"  t_after_power   = {op['measurement_time_after_power_s']:.1f} s")

    if runner.simulation_mode not in ["off", "hybrid"]:
        print("Full simulation mode: skipping hardware setup.")
        return

    for tag, value in VALVE_REACTION_TAGS:
        runner.opc.write_value(tag, value)

    # OPC pump setpoints are in uL/min.
    runner.opc.write_value(PUMP4_TAG, round(op["flow_pump4_ml_min"] * 1000.0, 0))
    runner.opc.write_value(PUMP3_TAG, round(op["flow_pump3_ml_min"] * 1000.0, 0))

    print(f"filling electrochemical cell for {op['filling_time_cell_s']:.1f} seconds")
    time.sleep(max(0.0, float(op["filling_time_cell_s"])))

    # Set galvanostatic current setpoint.
    runner.set_current(op["total_current_A"])
    runner.turn_on_power_supply()

    _countdown_to_measurement(runner, op["measurement_time_after_power_s"])


def measurement_tag(runner, parameters):
    # Keep protocol explicit; change here if FT-IR tag differs for DDM campaign.
    return "OpusOPCSvr.HP-CZC3484P17->PDA - mM"


def calculate_real_result(runner, mean_measurement, parameters, objectives, directions):
    op = _compute_operating_point(parameters)
    return calculate_objectives(
        raw_area=float(mean_measurement),
        substrate_concentration=op["substrate_target_mM"],
        selected_objectives=objectives,
        directions=directions,
    )


def autosampler_flow_rate(runner, parameters):
    op = _compute_operating_point(parameters)
    return float(op["total_flow_ml_min"])


def cleanup(runner, parameters):
    pass  # No cleanup actions needed for this protocol.
