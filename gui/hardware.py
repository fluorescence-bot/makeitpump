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
from ismatec.util import Tubing

DEBUGGING = False

if DEBUGGING:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('ismatec').setLevel(logging.DEBUG)
    from ismatec.mock import Pump
else:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('ismatec').setLevel(logging.DEBUG)
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

# Tubing inner diameters (mm) the pump accepts. The pump turns the diameter into
# its max flow, and the controller runs at 90 % of that, so a wrong size makes
# every dose too slow or too fast.
TUBING_MM = list(Tubing)

# How long to wait for the pump to answer a tubing query/change.
TUBING_TIMEOUT_S = 10

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


async def _get_tubing() -> Dict[str, Dict[str, float]]:
    await _init_hardware()
    mm, max_flow = {}, {}
    for name, ch in CHANNELS.items():
        mm[name] = await pump.get_tubing_inner_diameter(ch)
        reply = await pump.get_max_flow_rate(ch, calibrated=True)
        max_flow[name] = float(reply.split(' ')[0])
    return {"mm": mm, "max_ml_per_min": max_flow}


async def _set_tubing(mm: float) -> Dict[str, Dict[str, float]]:
    await _init_hardware()
    for ch in CHANNELS.values():
        if not await pump.set_tubing_inner_diameter(ch, mm):
            raise RuntimeError(f"Pump refused tubing {mm} mm on channel {ch}")
    return await _get_tubing()


def get_tubing() -> Dict[str, Dict[str, float]]:
    """Tubing inner diameter (mm) and calibrated max flow (mL/min) of each
    channel A-D, as stored on the pump."""
    return _submit(_get_tubing()).result(TUBING_TIMEOUT_S)


def set_tubing(mm: float) -> Dict[str, Dict[str, float]]:
    """Set the tubing inner diameter of every channel to `mm` (one of
    TUBING_MM), then return `get_tubing()`. Don't call it while a step runs."""
    return _submit(_set_tubing(mm)).result(TUBING_TIMEOUT_S)
