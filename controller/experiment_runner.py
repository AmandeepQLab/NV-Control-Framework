class ExperimentRunner:

    def __init__(self):
        self.running = False

    def run(self, experiment):
        self.running = True

        print("\n[Runner] Starting experiment...\n")

        result = experiment.run()

        print("\n[Runner] Experiment finished\n")

        self.running = False
        return result