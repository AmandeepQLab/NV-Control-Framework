from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

hw = HardwareManager(cfg)
hw.initialize()

input("Everything initialized. Press Enter...")

hw.shutdown()