# Example objective functions for VOL simulation mode

def normalized_area(raw_area, flow_aq, flow_org):
    """
    Normalize the FT-IR area based on flow dilution
    """
    norm_area = raw_area * (flow_org/flow_aq)
    
    return norm_area

def throughput(norm_area, residence_time):
    """
    Productivity: normalized area per unit time (signal/sec)
    """
    return norm_area / residence_time

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

def yield_proxy(raw_area):
    """
    Simple proxy for yield using raw FT-IR area only
    """
    return raw_area

def simulate_objectives(raw_area, flow_aq, flow_org, residence_time, selected_objectives=None, directions=None):
    """
    Compute selected objectives for simulation mode.
    """
    norm_area = normalized_area(raw_area, flow_aq, flow_org)

    all_objectives = {
        "Yield": yield_proxy(raw_area),
        "Normalized Area": norm_area,
        "Throughput": throughput(norm_area, residence_time),
        "Used Organic": used_organic(flow_org, residence_time),
        "Solvent Penalty": solvent_penalty(norm_area, flow_org, residence_time),
        "Extraction Efficiency": extraction_efficiency(norm_area, flow_org)
    }

    result = {}
    selected = selected_objectives if selected_objectives else all_objectives.keys()

    for key in selected:
        value = all_objectives.get(key)
        if directions and directions.get(key) == "minimize":
            value = -value
        result[key] = value

    return result
