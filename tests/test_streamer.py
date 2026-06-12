from sequencing.pulse_sequence import PulseSequence
from hardware.pulse_streamer.sim_pulse_streamer import SimPulseStreamer


seq = PulseSequence()

seq.add_digital_pulse(0, 0, 5000)
seq.add_digital_pulse(1, 1000, 3000)
seq.add_digital_pulse(2, 4500, 100)


streamer = SimPulseStreamer()
streamer.load_sequence(seq)
streamer.run()