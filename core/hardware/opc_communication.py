#opc_communication.py
import requests
import json
import time

class OPCClient:
    def __init__(self, server_url):
        """Initialize OPC Client with the given server URL."""
        self.server_url = server_url

    def read_value(self, item):
        """Reads a value from the OPC server."""
        try:
            response = requests.get(f"{self.server_url}/read?item={item}")
            response.raise_for_status()
            data = json.loads(response.text)
            return data.get("data", [{}])[0].get("Value", None)
        except requests.exceptions.RequestException as e:
            print(f"Error reading from OPC: {e}")
            return None

    def write_value(self, item, value):
        """Writes a value to the OPC server."""
        try:
            response = requests.get(f"{self.server_url}/write?item={item}&value={value}")
            response.raise_for_status()
            print(f"Successfully wrote {value} to {item}")
        except requests.exceptions.RequestException as e:
            print(f"Error writing to OPC: {e}")

    def check_connection(self, test_item):
        """Checks if the OPC server is reachable."""
        value = self.read_value(test_item)
        if value is not None:
            print("✅ OPC Connection Successful")
            return True
        else:
            print("❌ OPC Connection Failed")
            return False
