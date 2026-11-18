# Example objective functions for VOL simulation mode

import math


DEGRADATION_HELP = (
    "Degradation (%) = 100 × (1 − Cout/Cin). Use calibrated residual-substrate "
    "concentrations in matching units. Cout includes any analytical dilution correction. "
    "Higher values mean greater removal. UV/VIS reports apparent degradation/decolourization, "
    "not mineralization."
)


def degradation_percent(outlet_concentration, inlet_concentration):
    """Apparent substrate removal; retain negative results instead of clipping."""
    if isinstance(inlet_concentration, bool) or isinstance(outlet_concentration, bool):
        raise ValueError("Degradation requires numerical inlet and outlet concentrations.")
    inlet = float(inlet_concentration)
    outlet = float(outlet_concentration)
    if not math.isfinite(inlet) or inlet <= 0:
        raise ValueError("Degradation requires a finite inlet concentration greater than zero.")
    if not math.isfinite(outlet) or outlet < 0:
        raise ValueError("Degradation requires a finite, nonnegative outlet concentration.")
    value = 100.0 * (1.0 - outlet / inlet)
    if not math.isfinite(value):
        raise ValueError("Calculated degradation is not finite.")
    return value


def area(raw_area):
    """
    Raw Value obtained directly from the FT-IR.
    """
    return raw_area

def norm_area(raw_area, flow_aq, flow_org):
    """
    Normalize the FT-IR area based on flow dilution
    """
    norm_area = raw_area * (flow_org/flow_aq)
    
    return norm_area

def throughput(raw_area, flow_org, Molar_mass = 114.1):
    """
    Determine the throughput of the process.
    Throughput is defined as the amount of product per time.
    Units: mg of product per minute. 
    """
    return raw_area * flow_org * Molar_mass

def used_organic(flow_org, residence_time):
    """
    Total organic solvent used per experiment (mL)
    """
    return flow_org * residence_time / 60

def solvent_penalty(norm_area, flow_org, residence_time, weight=10):
    """
    Custom objective: balance yield with solvent usage
    """
    return norm_area - weight * used_organic(flow_org, residence_time)

def extraction_efficiency(norm_area, flow_org):
    """
    Signal related to the use of organic
    """
    return norm_area / flow_org

def yield_real(raw_area, flow_org, flow_aq):
    """
    Yield using a calibration curve and the flow of organic solvent.
    """
    return ((flow_org * raw_area) / ((flow_aq/2)*2.00)) * 100   

def space_time_yield(raw_area, flow_org, flow_aq, residence_time, reactor_volume=1.4):
    """
    Space-time yield (STY): amount of product per reactor volume per time.
    Units: (yield %) / (reactor_volume * residence_time)
    """
    yield_value = yield_real(raw_area, flow_org, flow_aq)
    return yield_value / (reactor_volume * residence_time)


def calculate_objectives(raw_area, substrate_concentration,  flow_aq = 1.0, flow_org = 1.0, residence_time = 1.0, selected_objectives=None, directions=None):
    """
    Compute selected objectives
    """

    include_degradation = selected_objectives is None or "Degradation" in selected_objectives
    degradation = degradation_percent(raw_area, substrate_concentration) if include_degradation else None

    # Precompute shared values
    product_concentration = raw_area

    normalized_area = norm_area(raw_area, flow_aq, flow_org)
    computed = {
        "Yield": product_concentration/substrate_concentration * 100,
        "Area": normalized_area,
        "Concentration": raw_area, 
        "Throughput": throughput(raw_area, flow_org),
        "Used Organic": used_organic(flow_org, residence_time),
        "Solvent Penalty": solvent_penalty(normalized_area, flow_org, residence_time),
        "Extraction Efficiency": extraction_efficiency(normalized_area, flow_org),
        "Space-Time Yield": space_time_yield(raw_area, flow_org, flow_aq, residence_time)
    }

    if include_degradation:
        computed["Degradation"] = degradation

    result = {}
    selected = selected_objectives if selected_objectives else computed.keys()

    for key in selected:
        if key not in computed:
            continue
        value = computed[key]
        if directions and directions.get(key) == "minimize":
            value = -value
        result[key] = float(value)

    return result
