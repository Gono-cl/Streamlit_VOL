"""Bounded flow-dilution measurements; pump access is supplied by a protocol."""
from __future__ import annotations

import math
import time

from core.hardware.uvvis_measurement import MeasurementValidationError


def validate_dilution_settings(settings, provider):
    """Validate configuration before a protocol is allowed to start hardware."""
    config = dict(settings)
    for key in ("reaction_flow_ml_min", "inlet_concentration_mg_L", "max_water_flow_ml_min", "settle_time_s"):
        if isinstance(config.get(key), bool):
            raise ValueError(f"Invalid automatic dilution setting: {key}.")
        value = float(config[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Automatic dilution {key} must be finite and positive.")
        config[key] = value
    attempts = config["max_attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 20:
        raise ValueError("Automatic dilution max_attempts must be an integer from 1 to 20.")
    if provider.concentration_unit != "mg/L":
        raise ValueError("MB automatic dilution requires a calibration and measurement unit of mg/L.")
    lower, upper = float(provider.sample.range_min), float(provider.sample.range_max)
    if not math.isfinite(lower) or not math.isfinite(upper) or not 0 <= lower < upper:
        raise ValueError("MB automatic dilution requires a nonnegative, ordered calibration range.")
    config["target_concentration_mg_L"] = (lower + upper) / 2.0
    return config


def collect_with_dilution(provider, settings, set_water_flow, on_attempt):
    """Measure directly, then bracket an in-range reading with bounded water flow.

    Above/below-range concentrations are not accepted or used to compute a
    final concentration. Inlet concentration supplies only an initial dilution
    hint; subsequent range flags bracket the factor. Every acquisition begins
    after the flow wait, so the provider rejects spectra exported during it.
    """
    config = validate_dilution_settings(settings, provider)
    reaction_flow = config["reaction_flow_ml_min"]
    maximum = config["max_water_flow_ml_min"]
    maximum_factor = 1.0 + maximum / reaction_flow
    requested_factor = 1.0
    lower_factor, upper_factor = 1.0, None
    current_water = 0.0
    history = []
    final = None
    error = None
    try:
        for attempt in range(1, config["max_attempts"] + 1):
            requested_water = min(maximum, max(0.0, reaction_flow * (requested_factor - 1.0)))
            # The setter returns the verified command flow, including rounding.
            current_water = float(set_water_flow(requested_water))
            if not math.isfinite(current_water) or not 0 <= current_water <= maximum:
                raise MeasurementValidationError("Dilution pump reported a flow outside the allowed limit.")
            if requested_water > 0 and current_water <= 0:
                raise MeasurementValidationError("Dilution water flow was not applied.")
            if requested_water == 0 and current_water != 0:
                raise MeasurementValidationError("Direct UV/VIS measurement requires Pump 3 stopped.")
            factor = 1.0 + current_water / reaction_flow
            context = {
                "attempt": attempt, "dilution_factor": factor,
                "reaction_flow_ml_min": reaction_flow, "water_flow_ml_min": current_water,
                "total_flow_ml_min": reaction_flow + current_water,
                "settle_time_s": config["settle_time_s"],
                "max_water_flow_ml_min": maximum, "max_attempts": config["max_attempts"],
            }
            print(f"UV/VIS attempt {attempt}/{config['max_attempts']}: Pump 3 "
                  f"{current_water:.6g} mL/min, dilution factor {factor:.6g}; "
                  f"waiting {config['settle_time_s']:g} s before fresh spectra.")
            time.sleep(config["settle_time_s"])
            try:
                raw = provider.measure()
            except BaseException:
                on_attempt(None, list(provider.reader.acquisitions), context)
                raise
            result = dict(raw)
            measured = float(raw["concentration"])
            valid = bool(raw["accepted"] and raw["status"] == "valid" and math.isfinite(measured))
            result.update(
                measured_concentration=measured,
                corrected_concentration=measured * factor if valid else None,
                **context,
                dilution_status=("direct" if factor == 1 else "diluted") if valid else raw["status"],
                dilution_used_for_result=False, dilution_stop_verified=False,
            )
            history.append({
                "attempt": attempt, "dilution_factor": factor,
                "water_flow_ml_min": current_water, "status": raw["status"],
                "accepted": bool(raw["accepted"]),
                "stability_criterion": raw.get("stability_criterion", "cv"),
                "stability_threshold": raw.get("stability_threshold"),
                "absorbance_sd": raw.get("absorbance_sd"),
                "measured_concentration": measured if math.isfinite(measured) else None,
            })
            result["dilution_history"] = list(history)
            on_attempt(result, list(provider.reader.acquisitions), context)
            if not math.isfinite(measured):
                provider.require_valid(raw)
                raise MeasurementValidationError("Invalid UV/VIS acquisition.")
            if raw["status"] == "valid":
                result["corrected_concentration"] = provider.require_valid(raw) * factor
                if not math.isfinite(result["corrected_concentration"]):
                    raise MeasurementValidationError("Corrected UV/VIS concentration is not finite.")
                final = result
                break
            if raw["status"] == "above_range":
                lower_factor = max(lower_factor, factor)
                if upper_factor is None:
                    hint = config["inlet_concentration_mg_L"] / config["target_concentration_mg_L"]
                    requested_factor = min(maximum_factor, max(factor * 2.0, hint))
                else:
                    requested_factor = math.sqrt(lower_factor * upper_factor)
                if requested_factor <= factor + 1e-9:
                    raise MeasurementValidationError(
                        f"UV/VIS remains above calibration range at the Pump 3 limit "
                        f"of {maximum:g} mL/min (factor {factor:.6g}).")
            elif raw["status"] == "below_range" and factor > 1:
                upper_factor = factor if upper_factor is None else min(upper_factor, factor)
                requested_factor = math.sqrt(lower_factor * upper_factor)
                if requested_factor >= factor - 1e-9:
                    raise MeasurementValidationError("Cannot resolve an in-range dilution factor.")
            else:
                provider.require_valid(raw)  # Direct below-range readings cannot be fixed by water.
                raise MeasurementValidationError(f"Unsupported UV/VIS range status: {raw['status']}.")
        if final is None:
            raise MeasurementValidationError(
                f"No validated UV/VIS concentration after {config['max_attempts']} dilution attempts.")
    except BaseException as exc:
        error = exc
    finally:
        try:
            stopped_flow = float(set_water_flow(0.0))
            if not math.isfinite(stopped_flow) or stopped_flow != 0:
                raise MeasurementValidationError("Pump 3 stop could not be verified after UV/VIS measurement.")
            if final is not None:
                final["dilution_stop_verified"] = True
            # Flush before subsequent normal sample collection. On a failed
            # measurement, return immediately to the runner's full shutdown.
            if error is None and current_water > 0:
                print(f"Pump 3 stopped; waiting {config['settle_time_s']:g} s for undiluted flow.")
                time.sleep(config["settle_time_s"])
        except BaseException as stop_error:
            if error is None:
                error = stop_error
            else:
                print(f"Pump 3 shutdown after measurement failure also failed: {stop_error}")
    if error is not None:
        raise error
    final["dilution_used_for_result"] = True
    print(f"Validated UV/VIS outlet concentration: {final['corrected_concentration']:.6g} mg/L "
          f"(detector {final['measured_concentration']:.6g} mg/L × {final['dilution_factor']:.6g}).")
    return final
