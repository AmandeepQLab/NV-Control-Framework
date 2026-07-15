from hardware.interfaces import SerialDevice


def main():

    device = SerialDevice("COM1")

    print(device.address)

    print(device.baudrate)

    print(device.timeout)


if __name__ == "__main__":

    main()