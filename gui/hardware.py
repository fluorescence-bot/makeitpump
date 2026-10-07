"""
Pump control for RainMixer, using Andreas' DilutionController and the
`ismatec` library (drivs/ismatec-pump), set up the same way as in
`reglo_gui/reglo_gui.py`.

The library is async, so the Pump and controller live on their own asyncio
event loop in a background thread. The runner calls `run_step` from its own
thread and blocks until the step is done (or stopped with `stop_all`).
"""

import asyncio
import concurrent.futures
import logging
import threading
from typing import Dict, Optional

from dilution_controller import DilutionController

DEBUGGING = True

if DEBUGGING:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('ismatec').setLevel(logging.DEBUG)
    from ismatec.mock import Pump
else:
    from ismatec import Pump

# Use COMx on Windows. NOTE: must be a different port than the cuvette rotator.
PUMP_ADDRESS = "/dev/ttyACM0"

# Pump channel for each mixer channel: A/B/C pump into the bowl, D pumps it out.
CHANNELS = {"A": 1, "B": 2, "C": 3, "D": 4}

# Estimated flow of one channel. The controller runs every channel at 90 % of the
# pump's max flow; this is only used to estimate how long a dose takes (UI
# animations, block times, mL delivered before a Stop). Placeholder until the
# pump is calibrated. Must match flow.ts in the web UI.
FLOW_ML_PER_S = 1.0

pump = None
controller: Optional[DilutionController] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_step: Optional[concurrent.futures.Future] = None
_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Start the asyncio thread on first use."""
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
        return _loop


def _submit(coro) -> concurrent.futures.Future:
    """Run a coroutine on the asyncio thread."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop())


async def _init_hardware() -> None:
    global pump, controller
    if controller is None:
        # Create inside the running loop (the mock grabs the current loop).
        pump = Pump(PUMP_ADDRESS, debug=DEBUGGING)
        controller = DilutionController(pump)
        await controller.setup()


async def _run_step(step) -> None:
    await _init_hardware()
    await controller.run_step(step)


def run_step(volumes: Dict[str, float]) -> None:
    """Pump `volumes` (mL per channel A-D) all at once and block until every
    channel is done. Returns early, without error, if `stop_all` is called."""
    global _step
    step = [float(volumes.get(ch, 0) or 0) for ch in CHANNELS]
    if not any(v > 0 for v in step):
        return
    _step = _submit(_run_step(step))
    try:
        _step.result()
    except concurrent.futures.CancelledError:
        pass
    finally:
        _step = None


async def _stop_all() -> None:
    if pump is not None:
        for ch in pump.channels:
            await pump.stop(ch)


def stop_all() -> None:
    """Stop all pumps, cancelling the step in progress. Doesn't wait for the
    pump to answer, since it is called from the UI thread. (The mock pump
    can't be interrupted: it finishes the channel it is on.)"""
    step = _step
    if step is not None:
        step.cancel()
    if pump is not None:
        _submit(_stop_all())
