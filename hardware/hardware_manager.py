from hardware.microwave.sg386 import SG386

from hardware.camera.sim_camera import SimCamera

from hardware.sim_hardware import SimPulseStreamer

from hardware.pulse_streamer.swabian_pulse_streamer import SwabianPulseStreamer

from hardware.camera.andor_neo_andor3 import AndorNeoAndor3
from hardware.power_supply import (
    SimPowerSupply,
    E3631A,
    E3632A,
)
from hardware.magnet import Magnet

class HardwareManager:

    def __init__(self, config_manager):

        self.cfg = config_manager

        self.hardware = {}

    # =====================================================
    # CREATE POWER SUPPLY
    # =====================================================

    def create_power_supply(self, config):

        ps_type = config["type"].upper()

        # ---------------------------------------------
        # Simulated Power Supply
        # ---------------------------------------------

        if ps_type == "SIM":

            return SimPowerSupply(config)

        # ---------------------------------------------
        # Keysight E3631A
        # ---------------------------------------------

        elif ps_type == "E3631A":

            return E3631A(config)

        # ---------------------------------------------
        # Keysight E3632A
        # ---------------------------------------------

        elif ps_type == "E3632A":

            return E3632A(config)

        else:

            raise ValueError(
                f"Unknown power supply type: {ps_type}"
            )
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
        # =================================================
        # POWER SUPPLIES
        # =================================================

        helmholtz_cfg = self.cfg.get("Helmholtz")

        power_supplies = {}

        for axis in ["X", "Y", "Z"]:

            ps = self.create_power_supply(
                helmholtz_cfg[axis]
            )

            ps.connect()

            power_supplies[axis] = ps

        self.hardware["power_supplies"] = power_supplies
        
        # =================================================
        # MAGNET
        # =================================================

        magnet = Magnet(

            config=helmholtz_cfg,

            power_supplies=power_supplies,

            pulse_streamer=self.hardware["pulse_streamer"]

        )

        self.hardware["magnet"] = magnet
        # =================================================
        # HARDWARE SUMMARY (TEMPORARY)
        # =================================================

        print("\nAvailable hardware:\n")

        for name in self.hardware:

            print(f"  {name}")
    # =====================================================
    # GET HARDWARE
    # =====================================================

    def get_hardware(self):

        return self.hardware
    # =====================================================
    # SHUTDOWN
    # =====================================================

    def shutdown(self):

        print("\nShutting down hardware...")

        # --------------------------------------------
        # Magnet
        # --------------------------------------------

        if "magnet" in self.hardware:

            self.hardware["magnet"].disable()

        # --------------------------------------------
        # Power supplies
        # --------------------------------------------

        if "power_supplies" in self.hardware:

            for ps in self.hardware["power_supplies"].values():

                ps.disconnect()

        # --------------------------------------------
        # Microwave
        # --------------------------------------------

        if "microwave" in self.hardware:

            try:
                self.hardware["microwave"].disconnect()
            except AttributeError:
                pass

        # --------------------------------------------
        # Camera
        # --------------------------------------------

        if "camera" in self.hardware:

            try:
                self.hardware["camera"].disconnect()
            except AttributeError:
                pass

        print("Hardware shutdown complete.")
