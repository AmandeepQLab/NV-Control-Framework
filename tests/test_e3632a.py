"""
Test E3632A communication
"""

from hardware.power_supply import E3632A


def main():

    config = {
        "name": "Test E3632A",
        "address": "COM11",
        "type": "E3632A",
        "range": "P15V",
        "max_current": 5,
    }

    ps = E3632A(config)

    ps.connect()

    print()

    print("Connected :", ps.is_connected())

    print("IDN       :", ps.get_identification())

    ps.disconnect()

    print("Disconnected")


if __name__ == "__main__":

    main()