import time
import threading

from core.hardware.AMF_hardware.spm_pump import SPMPump


pump = SPMPump(
    auto_home=True,
    syringe_volume=5000
)

# 1. Fill syringe with water from port 1
pump.move_valve(1)
pump.set_flow_rate(5000)   # uL/min
pump.pickup(5000)          # uL

# 2. Switch to delivery port
pump.move_valve(4)

# 3. Set a slow delivery flow
pump.set_flow_rate(500)    # uL/min


def dispense_test():
    pump.dispense(5000)


thread = threading.Thread(
    target=dispense_test,
    daemon=True
)

thread.start()

print("Dispensing started.")

# Let it run for 20 seconds
time.sleep(20)

print("Stopping after 20 seconds...")

pump.hard_stop()

pump.get_plunger_position()