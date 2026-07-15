from hardware.power_supply import E3632A


config = {
    "name": "Test E3632A",
    "address": "COM11",
    "type": "E3632A",
    "range": "P15V",
    "max_current": 5,
}

ps = E3632A(config)

ps.connect()

print(ps.identify())

print("Output ON")
ps.output_on()

input("Press Enter...")

print("Set Current = 0.25 A")
ps.set_current(0.25)

input("Press Enter...")

print("Output OFF")
ps.output_off()

ps.disconnect()