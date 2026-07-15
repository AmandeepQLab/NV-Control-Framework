"""
Test E3631A
"""

from hardware.power_supply import E3631A


def main():

    config = {

        "name": "Test E3631A",

        "address": "COM12",

        "type": "E3631A",

        "channel": "P6V",

        "max_current": 5,

    }

    ps = E3631A(config)

    ps.connect()

    print(ps.get_identification())

    print("Output ON")
    ps.output_on()

    input("Press Enter...")

    print("Set Current = 0.25 A")
    ps.set_current(0.25)

    input("Press Enter...")

    print("Output OFF")
    ps.output_off()

    ps.disconnect()


if __name__ == "__main__":

    main()