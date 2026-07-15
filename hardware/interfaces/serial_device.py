"""
=====================================================
Serial Device
-----------------------------------------------------
Generic serial communication interface.

Provides low-level read/write/query methods for
devices communicating over RS-232.

Author:
    Amandeep + ChatGPT
=====================================================
"""

import serial


class SerialDevice:

    # =================================================
    # INITIALIZATION
    # =================================================

    def __init__(
        self,
        address,
        baudrate=9600,
        timeout=1,
    ):

        self.address = address

        self.baudrate = baudrate

        self.timeout = timeout

        self.serial = None

    # =================================================
    # CONNECTION
    # =================================================

    def connect(self):

        self.serial = serial.Serial(

            port=self.address,

            baudrate=self.baudrate,

            timeout=self.timeout,

        )

    def disconnect(self):

        if self.serial is not None:

            self.serial.close()

            self.serial = None

    # =================================================
    # WRITE
    # =================================================

    def write(
        self,
        command,
    ):

        self.serial.write(

            (command + "\n").encode("ascii")

        )

    # =================================================
    # READ
    # =================================================

    def read(self):

        return (

            self.serial.readline()

            .decode("ascii")

            .strip()

        )

    # =================================================
    # QUERY
    # =================================================

    def query(
        self,
        command,
    ):

        self.write(command)

        return self.read()
    
    # =================================================
    # FLUSH
    # =================================================

    def flush(self):

        if self.serial is not None:

            self.serial.reset_input_buffer()

            self.serial.reset_output_buffer()
    
    # =================================================
    # CONNECTION STATUS
    # =================================================

    def is_connected(self):

        return (
            self.serial is not None
            and self.serial.is_open
        )
    
    