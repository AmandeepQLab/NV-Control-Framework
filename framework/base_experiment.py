"""
=====================================================
Base Experiment
-----------------------------------------------------
Reusable base class for NV Control experiments.
=====================================================
"""

import time


class BaseExperiment:

    # =====================================================
    # EXPERIMENT STATES
    # =====================================================

    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"

    # =====================================================
    # INITIALIZATION
    # =====================================================

    def __init__(self, hardware, config=None):

        self.hardware = hardware
        self.config = config

        self.state = self.IDLE

        self.stop_requested = False

        self.start_time = None

        self.end_time = None

        # -------------------------------------------------
        # Backward compatibility
        # -------------------------------------------------

        self.hw = hardware

        self.running = True

    # =====================================================
    # SETUP
    # =====================================================

    def setup(self):

        self.state = self.READY

    # =====================================================
    # RUN
    # =====================================================

    def run(self):

        raise NotImplementedError

    # =====================================================
    # STOP
    # =====================================================

    def stop(self):

        self.stop_requested = True

        self.state = self.STOPPING

        self.running = False

    # =====================================================
    # CLEANUP
    # =====================================================

    def cleanup(self):

        self.state = self.FINISHED

    # =====================================================
    # STATUS
    # =====================================================

    def is_running(self):

        return self.state == self.RUNNING

    # =====================================================
    # ELAPSED TIME
    # =====================================================

    def elapsed_time(self):

        if (
            self.is_running()
            and self.start_time is not None
        ):

            return time.time() - self.start_time

        return 0
