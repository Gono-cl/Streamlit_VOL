# Example objective functions for VOL simulation mode

def normalized_area(raw_area, flow_aq, flow_org):
    """
    Normalize the FT-IR area based on flow dilution
    """
    norm_area = raw_area * (flow_org/flow_aq)
    
    return norm_area

def throughput(raw_area, residence_time, flow_organic, Molar_mass = 114.1):
    """
    Determine the throughput of the process.
    Throughput is defined as the amount of product per time.
    Units: mg of product per minute. 
    """
    return raw_area * flow_organic * Molar_mass

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

def simulate_objectives(raw_area, flow_aq, flow_org, residence_time, selected_objectives=None, directions=None):
    """
    Compute selected objectives for simulation mode.
    """
    norm_area = normalized_area(raw_area, flow_aq, flow_org)


    all_objectives = {
        "Yield": yield_real(raw_area, flow_org, flow_aq),
        "Normalized Area": norm_area,
        "Throughput": throughput(norm_area, residence_time),
        "Used Organic": used_organic(flow_org, residence_time),
        "Solvent Penalty": solvent_penalty(norm_area, flow_org, residence_time),
        "Extraction Efficiency": extraction_efficiency(norm_area, flow_org),
        "Space-Time Yield": space_time_yield(raw_area, flow_org, flow_aq, residence_time)  
    }

    result = {}
    selected = selected_objectives if selected_objectives else all_objectives.keys()

    for key in selected:
        value = all_objectives.get(key)
        if directions and directions.get(key) == "minimize":
            value = -value
        result[key] = value

    return result
