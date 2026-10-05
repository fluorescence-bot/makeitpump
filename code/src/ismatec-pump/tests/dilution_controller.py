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
        await self.pump.set_event_messaging(True)

    async def run_step(self, step, timeout: float = 600, poll: float = 0.1):
        active = [ch for ch, vol in enumerate(step, start=1) if vol > 0]
        if not active:
            return

        # 1. Configure every active channel (nothing moves yet)
        for ch in active:
            await self.pump.set_flow_rate(ch, self.rate)
            await self.pump.set_volume_setpoint(ch, step[ch - 1])

        # 2. Mark them running BEFORE starting, so the wait loop can't see a stale False
        self.pump._set_running_status(True, active)

        # 3. Start back to back
        if len(active) == len(self.pump.channels):
            self.pump.hw.command('0H')            # broadcast: all channels at once
        else:
            for ch in active:
                await self.pump.start(ch)

        # 4. Wait until every started channel reports not running
        deadline = time.monotonic() + timeout
        while any(self.pump.running[ch] for ch in active):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Channels {active} still running after {timeout}s")
            await asyncio.sleep(poll)