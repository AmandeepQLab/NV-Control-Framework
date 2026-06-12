from config.config_manager import ConfigManager
from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer
from sequencing.pulse_sequence import PulseSequence


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ip = cfg.get("pulseGenerator", "ipAddress")

ps = SwabianPulseStreamer(ip)
ps.connect()

seq = PulseSequence()

# Channel 5 HIGH for 2 seconds
seq.add_pulse(
    channel=5,
    start_ns=0,
    duration_ns=2_000_000_000
)

ps.load_sequence(seq)
ps.run()

print("Sent 2 s HIGH pulse on Swabian CH5.")
