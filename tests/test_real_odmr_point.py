from config.config_manager import ConfigManager
from hardware.hardware_manager import HardwareManager
from experiments.odmr_experiment import ODMRExperiment


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

hw_manager = HardwareManager(cfg)
hw_manager.initialize()

hw = hw_manager.get_hardware()

config = {
    "roi": None,
    "exposure_s": 0.02,
    "trigger_delay_s": 0.1,
    "camera_gate_s": 0.5,
    "mw_power_dbm": -10
}

exp = ODMRExperiment(hw, config)
seq = exp.build_sequence()
seq.print_sequence()
hw["pulse_streamer"].load_sequence(seq)
print(hw["pulse_streamer"].compile_sequence())

frequency = 2.87e9

print("Acquiring one ODMR point at", frequency)

signal = exp.acquire_point(frequency)

print("Normalized ODMR signal:", signal)
