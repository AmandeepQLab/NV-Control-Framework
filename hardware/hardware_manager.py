from hardware.microwave.sg386 import SG386

from hardware.camera.sim_camera import SimCamera

from hardware.sim_hardware import SimPulseStreamer

from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer

from hardware.camera.andor_neo_andor3 import AndorNeoAndor3

class HardwareManager:

    def __init__(self, config_manager):

        self.cfg = config_manager

        self.hardware = {}

    # =====================================================
    # INITIALIZE ALL HARDWARE
    # =====================================================

    def initialize(self):
        # =================================================
        # CHANNEL MAP
        # =================================================

        ps_cfg = self.cfg.get("pulseGenerator")

        channel_names = ps_cfg["channelNames"]

        channel_values = ps_cfg["channelValues"]

        self.hardware["channels"] = dict(
            zip(channel_names, channel_values)
        )

        print("\nLoaded channel map:\n")

        for k, v in self.hardware["channels"].items():

            print(f"{k} -> {v}")

        # =================================================
        # MICROWAVE SOURCE
        # =================================================

        fg = self.cfg.get(
            "frequencyGenerators"
        )[0]

        fg_type = fg["type"].lower()

        # -------------------------------------------------
        # SRS SG386
        # -------------------------------------------------

        if fg_type == "srs":

            address = fg["address"]

            port = fg.get("port", None)

            mw = SG386(address, port)

            mw.connect()

            self.hardware["microwave"] = mw

        else:

            raise ValueError(
                f"Unknown microwave type: {fg_type}"
            )

        # =================================================
        # CAMERA
        # =================================================

        camera = AndorNeoAndor3()

        camera.connect()

        self.hardware["camera"] = camera

        # =================================================
        # PULSE STREAMER
        # =================================================

        ps_cfg = self.cfg.get("pulseGenerator")

        if ps_cfg["type"].lower() == "pulsestreamer":

            ip = ps_cfg["ipAddress"]

            pulse = SwabianPulseStreamer(ip)
            pulse.connect()

            self.hardware["pulse_streamer"] = pulse

        else:
            raise ValueError(
                f"Unknown pulse generator type: {ps_cfg['type']}"
            )

    # =====================================================
    # GET HARDWARE
    # =====================================================

    def get_hardware(self):

        return self.hardware
