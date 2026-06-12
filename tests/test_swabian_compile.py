from config.config_manager import ConfigManager
from sequencing.pulse_sequence import PulseSequence
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")
ip = ps_cfg["ipAddress"]

seq = PulseSequence()

seq.add_digital_pulse(channel=1, start_ns=0, duration_ns=5000)      # greenLaser
seq.add_digital_pulse(channel=2, start_ns=1000, duration_ns=3000)   # MW
seq.add_digital_pulse(channel=5, start_ns=4500, duration_ns=100)    # detector/camera

ps = SwabianPulseStreamer(ip)
ps.connect()

ps.load_sequence(seq)
ps.run()
