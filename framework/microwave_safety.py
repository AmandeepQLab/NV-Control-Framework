"""Microwave state outside ODMR: RF off, and a record of it.

The SG386 RF output is on only during an ODMR scan (ODMRExperiment switches
it on at the scan's first point and off when the scan ends). Before a Zero
Field run, ensure_rf_off() switches it off again explicitly -- in case
something left it on -- and returns what it then reads back, for the run's
metadata. GUI-free; works on any injected microwave object with rf_off()
(and, if present, is_rf_on() / get_frequency() / get_power()), so the sim
and the real driver are interchangeable.
"""

import logging

LOGGER = logging.getLogger(__name__)

RF_STATE_DEFINITIONS = {
    "sg386_connected": "an SG386 (or its simulator) was configured and connected",
    "sg386_rf_off_commanded": "rf_off() was sent at the start of this run without error",
    "sg386_rf_on": "RF output state just after that command (see sg386_rf_state_source)",
    "sg386_rf_state_source": "'read back' (queried from the instrument) or "
                             "'commanded' (the driver cannot read it back)",
    "sg386_frequency_hz": "frequency setting read from the SG386 at run start",
    "sg386_power_dbm": "amplitude setting read from the SG386 at run start",
    "mw_gate_channel": "Pulse Streamer channel of the MW switch (config 'MW')",
    "mw_gate_high": "MW switch channel level at run start, from the Pulse "
                    "Streamer's persistent-output cache (the level it idles "
                    "at; the device has no per-channel read-back)",
}


def ensure_rf_off(microwave, pulse_streamer=None, gate_channel=None):
    """Switch the RF output off and return the state for metadata.

    Never raises: a failure is logged and recorded (sg386_error). The
    caller decides with rf_off_problem(); a Zero Field run is refused when an
    SG386 is connected but RF could not be confirmed off.
    """
    record = {"sg386_connected": microwave is not None}
    if microwave is not None:
        try:
            microwave.rf_off()
            record["sg386_rf_off_commanded"] = True
        except Exception as error:
            LOGGER.exception("Could not switch the SG386 RF output off")
            record["sg386_rf_off_commanded"] = False
            record["sg386_error"] = repr(error)

        read_rf = getattr(microwave, "is_rf_on", None)
        if callable(read_rf):
            try:
                record["sg386_rf_on"] = bool(read_rf())
                record["sg386_rf_state_source"] = "read back"
            except Exception as error:
                LOGGER.exception("Could not read the SG386 RF output state")
                record["sg386_rf_on"] = None
                record["sg386_rf_state_source"] = f"read-back failed: {error!r}"
        else:
            record["sg386_rf_on"] = False if record["sg386_rf_off_commanded"] else None
            record["sg386_rf_state_source"] = "commanded"
        if record["sg386_rf_on"]:
            LOGGER.error("SG386 RF output still reads ON after rf_off()")

        for key, name in (("sg386_frequency_hz", "get_frequency"),
                          ("sg386_power_dbm", "get_power")):
            getter = getattr(microwave, name, None)
            try:
                record[key] = None if getter is None else float(getter())
            except Exception:
                LOGGER.exception("Could not read SG386 %s", name)
                record[key] = None

    if pulse_streamer is not None and gate_channel is not None:
        record["mw_gate_channel"] = gate_channel
        try:
            record["mw_gate_high"] = bool(pulse_streamer.get_digital_output(gate_channel))
        except Exception:
            LOGGER.exception("Could not read the MW switch channel state")
            record["mw_gate_high"] = None
    record["microwave_state_definitions"] = {
        key: text for key, text in RF_STATE_DEFINITIONS.items() if key in record
    }
    return record


def rf_off_problem(record):
    """Why a Zero Field run must not start, from ensure_rf_off()'s record,
    or None. Refused when an SG386 is connected but its RF output could not
    be switched off or confirmed off (command failed, read-back failed, or
    it still reads ON). No SG386 at all is fine: no RF is possible."""
    if not record.get("sg386_connected"):
        return None
    if not record.get("sg386_rf_off_commanded"):
        return ("The SG386 RF output could not be switched off "
                f"({record.get('sg386_error', 'unknown error')}). "
                "The Zero Field run was not started.")
    if record.get("sg386_rf_on") is None:
        return ("The SG386 RF output state could not be read back "
                f"({record.get('sg386_rf_state_source', 'read-back failed')}). "
                "The Zero Field run was not started.")
    if record.get("sg386_rf_on"):
        return ("The SG386 RF output still reads ON after switching it off. "
                "The Zero Field run was not started.")
    return None
