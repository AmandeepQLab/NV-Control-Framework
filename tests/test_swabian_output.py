from config.config_manager import ConfigManager
from sequencing.pulse_sequence import PulseSequence
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer

cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ip = cfg.get("pulseGenerator", "ipAddress")

seq = PulseSequence()

# Very short safe test pulse on greenLaser channel from JSON: channel 1
seq.add_digital_pulse(channel=1, start_ns=0, duration_ns=1000000)  # 1 ms

ps = SwabianPulseStreamer(ip)
ps.connect()
ps.load_sequence(seq)
ps.run()
