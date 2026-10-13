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

        self.connect()

        if auto_home:
            self.ensure_homed()


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

        print(
            f"SPM valve position: "
            f"{position}"
        )

        return position


    def set_flow_rate(
        self,
        flow_rate
    ):

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

        print(
            "Flow rate:",
            self.pump.getFlowRate()
        )


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

        if self.pump is not None:

            try:

                self.pump.disconnect()

                print(
                    "SPM pump disconnected."
                )

            except Exception as exc:

                print(
                    "Disconnect warning:",
                    exc
                )