class BaseExperiment:

    def __init__(self, hardware, config):

        self.hw = hardware
        self.config = config

        self.running = True

    # =====================================================
    # CONTROL
    # =====================================================

    def stop(self):

        self.running = False

    # =====================================================
    # REQUIRED METHODS
    # =====================================================

    def build_sequence(self):

        raise NotImplementedError

    def run(self):

        raise NotImplementedError