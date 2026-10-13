import time

from multiport_valve import (
    MultiportValve
)

from spm_pump import (
    SPMPump
)


# =========================================================
# TEST SETTINGS
# =========================================================

EXTERNAL_VALVE_PORT = 5

WAIT_TIME = 5
# seconds

SYRINGE_VOLUME = 5000
# µL

FLOW_RATE = 1000

SPM_VALVE_PORT = 1

PICKUP_VOLUME = 1000
# µL


# =========================================================
# MAIN TEST
# =========================================================

def main():

    valve = None
    pump = None

    try:

        print()
        print(
            "========================================"
        )

        print(
            "AMF MULTI-DEVICE TEST"
        )

        print(
            "========================================"
        )


        # -------------------------------------------------
        # Connect external multiport valve
        # -------------------------------------------------

        valve = MultiportValve(
            auto_home=True,
            max_port=12
        )


        # -------------------------------------------------
        # Connect SPM pump
        # -------------------------------------------------

        pump = SPMPump(
            auto_home=True,
            syringe_volume=
            SYRINGE_VOLUME
        )


        # -------------------------------------------------
        # Step 1
        # Move external rotary valve
        # -------------------------------------------------

        print()
        print(
            "STEP 1"
        )

        valve.move_to(
            EXTERNAL_VALVE_PORT
        )


        # -------------------------------------------------
        # Step 2
        # Wait
        # -------------------------------------------------

        print()
        print(
            "STEP 2"
        )

        print(
            f"Waiting "
            f"{WAIT_TIME} seconds..."
        )

        time.sleep(
            WAIT_TIME
        )


        # -------------------------------------------------
        # Step 3
        # Configure SPM pump
        # -------------------------------------------------

        print()
        print(
            "STEP 3"
        )

        pump.set_flow_rate(
            FLOW_RATE
        )


        # -------------------------------------------------
        # Step 4
        # Move SPM internal valve
        # -------------------------------------------------

        print()
        print(
            "STEP 4"
        )

        pump.move_valve(
            SPM_VALVE_PORT
        )


        # -------------------------------------------------
        # Step 5
        # Pickup volume
        # -------------------------------------------------

        print()
        print(
            "STEP 5"
        )

        pump.pickup(
            PICKUP_VOLUME
        )


        # -------------------------------------------------
        # Status
        # -------------------------------------------------

        print()
        print(
            "========================================"
        )

        print(
            "FINAL STATUS"
        )

        print(
            "========================================"
        )

        valve.status()

        pump.status()


        print()
        print(
            "Test completed successfully."
        )


    except Exception as exc:

        print()
        print(
            "ERROR:"
        )

        print(
            exc
        )


    finally:

        print()
        print(
            "Disconnecting hardware..."
        )

        if valve is not None:

            valve.disconnect()

        if pump is not None:

            pump.disconnect()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()