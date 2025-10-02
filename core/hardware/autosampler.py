import time
from core.hardware.opc_communication import OPCClient

class AutoSampler:
    def __init__(self, opc_client: OPCClient, vial_volume_ml=2.0):
        self.opc = opc_client
        self.current_position = None
        self.vial_volume = vial_volume_ml

    def move_to_position(self, pos):
        """Move autosampler to target position and return the final position."""
        self.opc.write_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.POS_T", pos)
        pos_out = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.T_POS")
        
        while True:
            pos_out = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.T_POS")
            if pos_out == pos:
                print(f"✅ Autosampler reached target position {pos}")
                return pos_out  # return the exact position

            else:
                print(f"⏳ Waiting for autosampler to move... (current: {pos_out})")
            time.sleep(1)
        

    def needle_down(self):    
        """Lower the needle and wait until done."""
        self.opc.write_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.POS_N", 3000)
        needle_stat = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.N_STAT")

        while True:
            needle_stat = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.N_STAT")

            if needle_stat == 1:
                print("✅ Needle down")
                return True
            else:
                print("⏳ Waiting for needle to lower...")
            time.sleep(1)
            
    def needle_up(self):    
        """Raise the needle and wait until done."""
        self.opc.write_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.POS_N", 0)
        needle_stat = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.N_STAT")

        while True:
            needle_stat = self.opc.read_value("Hitec_OPC_DA20_Server->DIAZOAN:AUTOSAMPLER.N_STAT")

            if needle_stat == 0:
                print("✅ Needle raised")
                return True

            else:
                print("⏳ Waiting for needle to raise...")
            time.sleep(1)
        
    def set_valve_collect(self):
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 0)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 0)
        print("🔀 Valve switched to COLLECT")

    def set_valve_waste(self):
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_CLOSE", 1)
        self.opc.write_value("Hitec_OPC_DA20_Server-%3EDIAZOAN%3AV_02_OPEN", 1)
        print("🔀 Valve switched to WASTE")
    
    def move_prepare_needle(self, pos):
        print(f"=== Start moving to position {pos} ===")
        self.move_to_position(pos)
        self.needle_down()
        print(f"=== Ready for collection on {pos} ===")

    def clean_before_collect(self, pos):
        print(f"=== Start moving to position {pos} ===")
        self.move_to_position(pos)
        self.needle_down()
        print(f"=== Start disposing to vial on {pos} ===")
        self.set_valve_collect()
        time.sleep(10)  # Simulate cleaning time
        self.set_valve_waste()
        self.needle_up()
        print(f"=== Cleaning finished ===")

    def start_collection(self, flow_rate, volume):
        (f"=== Start collecting sample to position ===")

        self.set_valve_collect()

        collected_volume = 0.0
        t0 = time.time()
        while collected_volume < self.vial_volume and collected_volume < volume:
            elapsed_min = (time.time() - t0) / 60
            collected_volume = elapsed_min * flow_rate
            print(f"📊 Collected {collected_volume:.2f} ml")
            time.sleep(2)

        print("🧪 Vial is full or we reached the desire volume")
        self.set_valve_waste()
        self.needle_up()
        print(f"=== Sample collection finished ===")
        return True