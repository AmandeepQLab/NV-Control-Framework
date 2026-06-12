from sequencing.pulse_sequence import PulseSequence


seq = PulseSequence()

# Laser / AOM pulse
seq.add_digital_pulse(
    channel=0,
    start_ns=0,
    duration_ns=5000
)

# Microwave gate
seq.add_digital_pulse(
    channel=1,
    start_ns=1000,
    duration_ns=3000
)

# Camera trigger
seq.add_digital_pulse(
    channel=2,
    start_ns=4500,
    duration_ns=100
)

seq.print_sequence()