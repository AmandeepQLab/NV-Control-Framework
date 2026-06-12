import pyvisa


class SG386:

    def __init__(self, address, port=None):
        self.address = address
        self.port = port

        # VISA-LAN resource
        self.resource_name = f"TCPIP0::{address}::inst0::INSTR"

        self.rm = pyvisa.ResourceManager()
        self.inst = None

        self.frequency = None
        self.power = None

    def connect(self):
        self.inst = self.rm.open_resource(self.resource_name)
        self.inst.timeout = 5000

        idn = self.inst.query("*IDN?")
        print("[SG386] Connected:", idn.strip())
        
    def set_frequency(self, frequency_hz):
        self.frequency = frequency_hz
        self.inst.write(f"FREQ {frequency_hz} Hz")

    def get_frequency(self):
        return float(self.inst.query("FREQ?"))

    def set_power(self, power_dbm):
        self.power = power_dbm
        self.inst.write(f"AMPR {power_dbm} DBM")

    def get_power(self):
        return float(self.inst.query("AMPR?"))

    def rf_on(self):
        self.inst.write("ENBR 1")

    def rf_off(self):
        self.inst.write("ENBR 0")

    def close(self):
        if self.inst is not None:
            self.inst.close()
            self.inst = None
