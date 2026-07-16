from framework.base_experiment import BaseExperiment


class PulsedExperiment(BaseExperiment):

    def __init__(self, hardware, config):

        super().__init__(hardware, config)

    # =====================================================
    # PULSE SEQUENCE
    # =====================================================

    def build_sequence(self):

        raise NotImplementedError
