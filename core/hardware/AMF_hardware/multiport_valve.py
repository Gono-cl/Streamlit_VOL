import amfTools


class MultiportValve:

    def __init__(
        self,
        auto_home=True,
        max_port=12
    ):

        self.device = None
        self.valve = None

        self.max_port = max_port

        self.connect()

        if auto_home:
            self.ensure_homed()


    def connect(self):

        print(
            "Searching for RVMFS valve..."
        )

        devices = (
            amfTools.util.getProductList(
                connection_mode="USB",
                product_family="RVMFS"
            )
        )

        if len(devices) == 0:

            raise RuntimeError(
                "No RVMFS valve detected. "
                "Check USB connection and power."
            )

        if len(devices) > 1:

            print(
                f"Warning: "
                f"{len(devices)} RVMFS valves detected."
            )

            print(
                "Using the first detected valve."
            )

        self.device = devices[0]

        print()
        print(
            "Detected:"
        )

        print(
            self.device
        )

        self.valve = (
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
            "--- Multiport valve information ---"
        )

        print(
            "Connected:",
            self.valve.connected
        )

        print(
            "Type:",
            self.valve.getType()
        )

        print(
            "Family:",
            self.valve.getProductFamily()
        )

        print(
            "Serial:",
            self.valve.getSerialNumber()
        )

        print(
            "COM port:",
            self.valve.getSerialPort()
        )

        print(
            "Position:",
            self.valve.getValvePosition()
        )

        print(
            "Home status:",
            self.valve.getHomeStatus()
        )

        print(
            "Valve status:",
            self.valve.getValveStatus()
        )

        print(
            "-----------------------------------"
        )


    def ensure_homed(self):

        if not self.valve.getHomeStatus():

            print()
            print(
                "Valve is not homed."
            )

            print(
                "Homing valve..."
            )

            self.valve.home()

            print(
                "Home status:",
                self.valve.getHomeStatus()
            )

            print(
                "Position:",
                self.valve.getValvePosition()
            )

        else:

            print(
                "Valve already homed."
            )


    def home(self):

        print(
            "Homing valve..."
        )

        self.valve.home()

        print(
            "Home status:",
            self.valve.getHomeStatus()
        )

        print(
            "Position:",
            self.valve.getValvePosition()
        )


    def get_position(self):

        position = (
            self.valve.getValvePosition()
        )

        print(
            f"Current valve position: "
            f"{position}"
        )

        return position


    def move_to(
        self,
        port
    ):

        if not isinstance(
            port,
            int
        ):

            raise TypeError(
                "Port must be an integer."
            )

        if not 1 <= port <= self.max_port:

            raise ValueError(
                f"Port must be between "
                f"1 and {self.max_port}."
            )

        if not self.valve.getHomeStatus():

            self.ensure_homed()

        current_position = (
            self.valve.getValvePosition()
        )

        if current_position == port:

            print(
                f"Valve already at port "
                f"{port}."
            )

            return port

        print(
            f"Moving valve "
            f"{current_position} -> {port}"
        )

        self.valve.valveShortestPath(
            port
        )

        final_position = (
            self.valve.getValvePosition()
        )

        if final_position == port:

            print(
                f"Valve successfully moved "
                f"to port {port}."
            )

        else:

            print(
                f"Warning: requested "
                f"{port}, reported "
                f"{final_position}."
            )

        return final_position


    def status(self):

        data = {

            "connected":
                self.valve.connected,

            "position":
                self.valve.getValvePosition(),

            "homed":
                self.valve.getHomeStatus(),

            "status":
                self.valve.getValveStatus(),

            "serial":
                self.valve.getSerialNumber(),

            "port":
                self.valve.getSerialPort()
        }

        print()
        print(
            "--- Multiport valve status ---"
        )

        for key, value in data.items():

            print(
                f"{key}: {value}"
            )

        print(
            "------------------------------"
        )

        return data


    def disconnect(self):

        if self.valve is not None:

            try:

                self.valve.disconnect()

                print(
                    "Valve disconnected."
                )

            except Exception as exc:

                print(
                    "Disconnect warning:",
                    exc
                )