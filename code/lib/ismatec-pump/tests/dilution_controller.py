# Class for use with ismatec library (git@github.com:alexrudd2/ismatec.git)

from ismatec import Pump
from ismatec.util import Mode, Rotation, Setpoint, Tubing
import asyncio
import time


class DilutionController:
    """Middle layer: turns a sequence into pump commands, one step at a time."""

    def __init__(self, pump_api : Pump):
        self.pump = pump_api
        self._run = None

    def pause(self):
        pass
    
    async def setup(self):
        # use pump.channels instead of hardcoding 1-4
        for ch in self.pump.channels:
            await self.pump.set_mode(ch, Mode.VOL_AT_RATE)
            await self.pump.set_setpoint_type(ch, Setpoint.FLOWRATE)
        await self.pump.set_event_messaging(True)

    async def run_step(self, step, rate_fraction: float = 0.9, poll: float = 0.1):
        active = [ch for ch, vol in enumerate(step, start=1) if vol > 0]
        if not active:
            return

        # 1. Configure each channel (nothing moves yet)
        est_seconds = 0.0
        for ch in active:
            vol = step[ch - 1]
            reply = await self.pump.get_max_flow_rate(ch, calibrated=True)
            rate = float(reply.split(' ')[0]) * rate_fraction
            await self.pump.set_flow_rate(ch, rate)
            await self.pump.set_volume_setpoint(ch, vol)
            est_seconds = max(est_seconds, vol / rate * 60)

        # 2. Start one by one and check each one actually started
        failed = []
        for ch in active:
            if not await self.pump.start(ch):
                failed.append(ch)
        if failed:
            for ch in active:
                await self.pump.stop(ch)                      # don't leave the others running
            reasons = {ch: await self.pump.get_run_failure_reason(ch) for ch in failed}
            raise RuntimeError(f"Start failed (cause code, limit): {reasons}")

        # 3. Wait for every started channel to report not running
        deadline = time.monotonic() + est_seconds * 1.5 + 10
        next_check = time.monotonic() + 1.0
        while any(self.pump.running[ch] for ch in active):
            now = time.monotonic()
            if now > deadline:
                raise TimeoutError(f"Channels {active} still running after {est_seconds:.0f}s estimate")
            if now >= next_check:
                # Fallback: events can be missed, so ask the pump itself
                reply = self.pump.hw.query('1E')
                if reply.strip().startswith('-'):          # '-' = whole pump stopped
                    self.pump._set_running_status(False, active)
                    break
                next_check = now + 2.0
            await asyncio.sleep(poll)