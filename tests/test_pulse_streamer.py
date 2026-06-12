from config.config_manager import ConfigManager
from pulsestreamer import PulseStreamer


cfg = ConfigManager("config/setupInfo.json")
cfg.load()

ps_cfg = cfg.get("pulseGenerator")
ip = ps_cfg["ipAddress"]

print("Connecting to Pulse Streamer:", ip)

ps = PulseStreamer(ip)

print("Pulse Streamer connected successfully.")
print("IP:", ip)
