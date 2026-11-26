import logging
import json



LOGGER = logging.getLogger(__name__)

class ConfigManager:

    def __init__(self, config_path):

        self.config_path = config_path

        self.config = None

    # =====================================================
    # LOAD
    # =====================================================

    def load(self):

        with open(self.config_path, "r") as f:

            self.config = json.load(f)

        LOGGER.debug("Loaded config: %s", self.config_path)

    # =====================================================
    # GET
    # =====================================================

    def get(self, *keys):

        value = self.config

        for k in keys:

            value = value[k]

        return value