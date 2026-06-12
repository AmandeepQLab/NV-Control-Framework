from config.config_manager import ConfigManager

cfg = ConfigManager(
    "config/setupInfo.json"
)

cfg.load()

print()

print(
    "MW IP:",
    cfg.get(
        "frequencyGenerators",
        0,
        "address"
    )
)

print()

print(
    "Pulse Streamer IP:",
    cfg.get(
        "pulseGenerator",
        "ipAddress"
    )
)

print()

print(
    "MW channel:",
    cfg.get(
        "microwave",
        "MW",
        "switchChannelName"
    )
)