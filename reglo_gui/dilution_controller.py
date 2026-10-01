# Class for use with ismatec library (git@github.com:alexrudd2/ismatec.git)

class DilutionController:
    """Middle layer: turns a sequence into pump commands, one step at a time."""

    def __init__(self, pump_api):
        self.pump = pump_api
        self._run = None

    def start(self, sequence):
        """Called by the GUI with the sequence. Runs the first step."""
        self._run = self._dilution_steps(sequence)
        return self.next_step()

    def next_step(self):
        """Called by the GUI when the user is ready. Runs one step."""
        if self._run is None:
            return None
        try:
            return next(self._run)
        except StopIteration:
            self._run = None
            return None

    def abort(self):
        if self._run is not None:
            self._run.close()
            self._run = None

    def _dilution_steps(self, sequence):
        total = len(sequence)
        for i, (vol_a, vol_b) in enumerate(sequence, start=1):
            if vol_a > 0:
                self.pump.dispense_vol_at_rate(1, vol_a, 50)
            if vol_b > 0:
                self.pump.dispense_vol_at_rate(1, vol_b, 50)
            yield {"step": i, "of": total, "a_ml": vol_a, "b_ml": vol_b}
            # Frozen here until next_step() is called again