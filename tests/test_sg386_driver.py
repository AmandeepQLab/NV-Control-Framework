from config.config_manager import ConfigManager
from hardware.microwave.sg386 import SG386


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

fg = cfg.get("frequencyGenerators")[0]

address = fg["address"]
port = fg.get("port", None)

print("Using SRS from config:")
print("Address:", address)
print("Port:", port)

sg = SG386(address, port)
sg.connect()

sg.set_frequency(2.87e9)
print("Frequency:", sg.get_frequency())

sg.set_power(-10)
print("Power:", sg.get_power())

sg.rf_on()
input("RF ON. Press ENTER to turn RF OFF...")

sg.rf_off()
sg.close()
