from pulsestreamer import PulseStreamer

ps = PulseStreamer("132.64.56.138")


seq = ps.createSequence()
seq.setDigital(3, [(100000000,0)])
ps.stream(seq)

input("Relay LOW")

seq = ps.createSequence()
seq.setDigital(3, [(100000000,1)])
ps.stream(seq)

input("Relay HIGH")

seq = ps.createSequence()
seq.setDigital(3, [(100000000,0)])
ps.stream(seq)

input("Relay LOW")