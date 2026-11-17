import math
import time

import amfTools


class SPMPump:

    def __init__(
        self,
        auto_home=True,
        syringe_volume=5000
    ):

        self.device = None
        self.pump = None

        self.syringe_volume = (
            syringe_volume
        )

        try:
            self.connect()
            if auto_home:
                self.ensure_homed()
        except BaseException:
            try:
                self.disconnect()
            except Exception as close_error:
                print(f"SPM disconnection after initialization error also failed: {close_error}")
            raise


    def connect(self):

        print(
            "Searching for SPM pump..."
        )

        devices = (
            amfTools.util.getProductList(
                connection_mode="USB",
                product_family="Pump"
            )
        )

        if len(devices) == 0:

            raise RuntimeError(
                "No AMF pump detected. "
                "Check USB connection and power."
            )

        if len(devices) > 1:

            print(
                f"Warning: "
                f"{len(devices)} pumps detected."
            )

            print(
                "Using the first detected pump."
            )

        self.device = devices[0]

        print()
        print(
            "Detected:"
        )

        print(
            self.device
        )

        self.pump = (
            amfTools.AMF(
                self.device
            )
        )

        print()
        print(
            "Connected successfully."
        )

        self.info()


    def info(self):

        print()
        print(
            "--- SPM pump information ---"
        )

        print(
            "Connected:",
            self.pump.connected
        )

        print(
            "Type:",
            self.pump.getType()
        )

        print(
            "Family:",
            self.pump.getProductFamily()
        )

        print(
            "Serial:",
            self.pump.getSerialNumber()
        )

        print(
            "COM port:",
            self.pump.getSerialPort()
        )

        print(
            "Pump status:",
            self.pump.getPumpStatus()
        )

        print(
            "Plunger position:",
            self.pump.getPlungerPosition()
        )

        print(
            "Real plunger position:",
            self.pump.getRealPlungerPosition()
        )

        print(
            "Home status:",
            self.pump.getHomeStatus()
        )

        print(
            "Syringe size:",
            self.pump.getSyringeSize()
        )

        print(
            "Flow rate:",
            self.pump.getFlowRate()
        )

        print(
            "Speed:",
            self.pump.getSpeedPump()
        )

        print(
            "Valve position:",
            self.pump.getValvePosition()
        )

        print(
            "Valve status:",
            self.pump.getValveStatus()
        )

        print(
            "----------------------------"
        )


    def ensure_homed(
        self,
        force=1
    ):

        if not self.pump.getHomeStatus():

            print()
            print(
                "Pump is not homed."
            )

            self.home(
                force=force
            )

        else:

            print(
                "Pump already homed."
            )


    def home(
        self,
        force=1
    ):

        print(
            "Homing pump..."
        )

        self.pump.setPumpStrengthAndHome(
            force=force
        )

        print(
            "Home status:",
            self.pump.getHomeStatus()
        )

        print(
            "Plunger position:",
            self.pump.getPlungerPosition()
        )


    def move_valve(
        self,
        port
    ):

        if not isinstance(
            port,
            int
        ):

            raise TypeError(
                "Valve port must be an integer."
            )

        port_count = self.pump.getPortNumber()
        if isinstance(port, bool) or not isinstance(port_count, int) or not 1 <= port <= port_count:
            raise ValueError(f"SPM valve port {port!r} is outside the available ports (1–{port_count}).")

        print(
            f"Moving SPM internal valve "
            f"to port {port}..."
        )

        self.pump.valveShortestPath(
            port
        )

        position = (
            self.pump.getValvePosition()
        )

        if position != port:
            raise RuntimeError(f"SPM valve did not reach port {port}; reported position: {position!r}.")

        print(
            f"SPM valve position: "
            f"{position}"
        )

        return position


    def set_flow_rate(
        self,
        flow_rate
    ):

        flow_rate = float(flow_rate)
        if not math.isfinite(flow_rate) or flow_rate <= 0:
            raise ValueError("SPM flow rate must be a finite, positive value in µL/min.")

        print(
            f"Setting flow rate to "
            f"{flow_rate}..."
        )

        self.pump.setFlowRate(

            flowRate=
            flow_rate,

            syringeVolume=
            self.syringe_volume
        )

        self._flow_rate_uL_min = float(self.pump.getFlowRate())
        if not math.isfinite(self._flow_rate_uL_min) or self._flow_rate_uL_min <= 0:
            raise RuntimeError("SPM did not report a valid flow rate.")
        print("Flow rate:", self._flow_rate_uL_min)

    def _wait_until_idle(self, timeout_s=10.0):
        """Require an acknowledged stop/completion; never clear device faults here."""
        timeout_s = float(timeout_s)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("SPM timeout must be finite and positive.")
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.pump.getPumpStatus()
            if status == 0:
                return
            if status != 255:
                raise RuntimeError(f"SPM pump fault/status {status!r}; recover the device before continuing.")
            if time.monotonic() >= deadline:
                raise TimeoutError("SPM is still busy; no refill or valve movement is allowed.")
            time.sleep(0.05)

    def get_syringe_state(self):
        """Read the encoder position in the configured resolution, not µL or a target."""
        status = self.pump.getPumpStatus()
        if status != 0:
            raise RuntimeError(f"SPM is not ready (status {status!r}); stop/recover it before reading refill state.")
        if not self.pump.getHomeStatus():
            raise RuntimeError("SPM is not homed; complete the device initialization before refilling.")
        capacity = float(self.syringe_volume)
        if not math.isfinite(capacity) or capacity <= 0:
            raise ValueError("SPM syringe capacity must be finite and positive.")
        resolution = self.pump.getMicrostepResolution()
        if resolution not in (0, 1):
            raise RuntimeError(f"Unknown SPM resolution mode: {resolution!r}.")
        full_stroke_steps = 3000 if resolution == 0 else 24000
        actual_steps = float(self.pump.getRealPlungerPosition())
        if not math.isfinite(actual_steps) or not 0 <= actual_steps <= full_stroke_steps:
            raise RuntimeError(f"Invalid SPM encoder position: {actual_steps!r}.")
        return {
            "actual_steps": actual_steps,
            "commanded_steps": self.pump.getPlungerPosition(),
            "full_stroke_steps": full_stroke_steps,
            "volume_uL": capacity * actual_steps / full_stroke_steps,
            "tolerance_uL": capacity * 2.0 / full_stroke_steps,
        }

    def move_to_volume(self, volume_uL, block=True):
        """Move to an absolute syringe content, preventing cumulative overfilling."""
        volume_uL = float(volume_uL)
        capacity = float(self.syringe_volume)
        if not math.isfinite(volume_uL) or not 0 <= volume_uL <= capacity:
            raise ValueError(f"SPM target volume must be between 0 and {capacity:g} µL.")
        before = self.get_syringe_state()
        self.pump.pumpVolume(volume=volume_uL, syringeVolume=capacity, block=False)
        if not block:
            return None
        flow = getattr(self, "_flow_rate_uL_min", None)
        timeout = 120.0 if flow is None else abs(volume_uL - before["volume_uL"]) / flow * 60.0 + 15.0
        self._wait_until_idle(timeout_s=timeout)
        after = self.get_syringe_state()
        if abs(after["volume_uL"] - volume_uL) > after["tolerance_uL"]:
            raise RuntimeError(
                f"SPM did not reach {volume_uL:g} µL; encoder reports {after['volume_uL']:.2f} µL."
            )
        return after


    def pickup(
        self,
        volume
    ):

        if volume <= 0:

            raise ValueError(
                "Pickup volume must be "
                "greater than zero."
            )

        print(
            f"Picking up "
            f"{volume} µL..."
        )

        self.pump.pumpPickupVolume(

            volume=
            volume,

            syringeVolume=
            self.syringe_volume
        )

        position = (
            self.pump.getPlungerPosition()
        )

        print(
            "Plunger position after pickup:",
            position
        )

        return position


    def dispense(
        self,
        volume
    ):

        if volume <= 0:

            raise ValueError(
                "Dispense volume must be "
                "greater than zero."
            )

        print(
            f"Dispensing "
            f"{volume} µL..."
        )

        self.pump.pumpDispenseVolume(

            volume=
            volume,

            syringeVolume=
            self.syringe_volume
        )

        position = (
            self.pump.getPlungerPosition()
        )

        print(
            "Plunger position after dispense:",
            position
        )

        return position


    def get_plunger_position(self):

        position = (
            self.pump.getPlungerPosition()
        )

        print(
            f"Current plunger position: "
            f"{position}"
        )

        return position


    def status(self):

        data = {

            "connected":
                self.pump.connected,

            "pump_status":
                self.pump.getPumpStatus(),

            "plunger_position":
                self.pump.getPlungerPosition(),

            "real_plunger_position":
                self.pump.getRealPlungerPosition(),

            "homed":
                self.pump.getHomeStatus(),

            "syringe_size":
                self.pump.getSyringeSize(),

            "flow_rate":
                self.pump.getFlowRate(),

            "speed":
                self.pump.getSpeedPump(),

            "valve_position":
                self.pump.getValvePosition(),

            "valve_status":
                self.pump.getValveStatus(),

            "serial":
                self.pump.getSerialNumber(),

            "port":
                self.pump.getSerialPort()
        }

        print()
        print(
            "--- SPM pump status ---"
        )

        for key, value in data.items():

            print(
                f"{key}: {value}"
            )

        print(
            "-----------------------"
        )

        return data


    def disconnect(self):
        if self.pump is None:
            return
        self.pump.disconnect()
        if getattr(self.pump, "connected", False) is True:
            raise RuntimeError("SPM serial connection is still open after disconnect.")
        self.pump = None
        self.device = None
        print("SPM pump disconnected.")
    
    def hard_stop(self):

        print("Stopping SPM pump...")

        try:
            self.pump.hardStop()

        except KeyError as exc:
            print(
                "AMFTools status parsing warning after hardStop:",
                exc
            )

        self._wait_until_idle()
        state = self.get_syringe_state()
        print("Pump status: 0 (ready)")
        print("Actual plunger position:", state["actual_steps"])
        print("Commanded plunger position:", state["commanded_steps"])
        print(f"Syringe volume remaining: {state['volume_uL']:.2f} µL")
        return state
