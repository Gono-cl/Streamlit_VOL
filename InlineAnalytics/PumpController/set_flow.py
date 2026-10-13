import requests


class PumpController:

    def __init__(
        self,
        host="http://em-nun:57080",
        opc_area="E_CHEM"
    ):
        self.host = host
        self.opc_area = opc_area


    def set_flow(self, pump, flow):

        if flow < 0:
            raise ValueError(
                "Pump flow cannot be negative."
            )

        tag = (
            f"Hitec_OPC_DA20_Server->"
            f"{self.opc_area}:{pump}.W1"
        )

        # Important:
        # OPC Expert on this system expects
        # comma as decimal separator.
        flow_string = (
            f"{float(flow):.4f}"
            .replace(".", ",")
        )

        response = requests.get(
            f"{self.host}/write",
            params={
                "item": tag,
                "value": flow_string
            },
            timeout=10
        )

        response.raise_for_status()

        print(
            f"{pump} set to "
            f"{flow:.3f} mL/min "
            f"(sent as {flow_string})"
        )


    def stop(self, pump):

        self.set_flow(
            pump,
            0.0
        )


    def stop_all(self):

        for pump in [
            "PUMP1",
            "PUMP2",
            "PUMP3",
            "PUMP4"
        ]:

            self.stop(pump)
