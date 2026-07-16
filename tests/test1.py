from pulsestreamer import PulseStreamer, OutputState

ps = PulseStreamer("132.64.56.138")

input("HIGH")
ps.constant(OutputState([3]))

input("LOW")
ps.constant(OutputState([]))