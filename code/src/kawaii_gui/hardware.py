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
import os
from typing import Dict, List, Optional

import serial
from serial.tools import list_ports

import rotator
from dilution_controller import DilutionController
from ismatec import Pump as RealPump
from ismatec.mock import Pump as MockPump
from ismatec.util import Tubing

# Messages from the ismatec library reach the terminal at WARNING and above;
# `set_logging(True)` turns its debug output on.
logging.basicConfig(level=logging.WARNING)
_ismatec_log = logging.getLogger('ismatec')
_ismatec_log.setLevel(logging.WARNING)

# True: never look for the real pump, always use the ismatec mock (tests set this).
FORCE_MOCK = False

# Serial port of the pump, e.g. "COM3" or "/dev/ttyACM0". None: try every USB
# serial port (COMx on Windows, /dev/cu.usbmodem* on macOS, /dev/ttyACM* on
# Linux) except the cuvette rotator's Arduino Nano, and use the one that
# answers like a pump.
PUMP_ADDRESS: Optional[str] = None

# USB vendor IDs never tried as the pump by auto-detection: the ones rotator.py
# finds the Nano by. A pump on such a USB chip can still be chosen in the UI.
NOT_PUMP_VIDS = {vid for vid, _ in rotator.USB_IDS}

# Pump channel for each mixer channel: A/B/C pump into the bowl, D pumps it out.
CHANNELS = {"A": 1, "B": 2, "C": 3, "D": 4}

# Estimated flow of one channel. The controller runs every channel at 90 % of the
# pump's max flow; this is only used to estimate how long a dose takes (UI
# animations, block times, mL delivered before a Stop). Placeholder until the
# pump is calibrated. Must match flow.ts in the web UI.
FLOW_ML_PER_S = 1.0

# Range of tubing inner diameters (mm) the pump accepts (any value, 0.01 mm steps). The pump turns the diameter into
# its max flow, and the controller runs at 90 % of that, so a wrong size makes
# every dose too slow or too fast.
TUBING_MIN_MM = Tubing[0]    # 0.13
TUBING_MAX_MM = Tubing[-1]   # 3.17

# How long to wait for the pump to answer a tubing query/change.
TUBING_TIMEOUT_S = 10

# How long the real pump gets to answer its first query while it is being
# detected. A pump that doesn't answer counts as not found.
PUMP_TIMEOUT_S = 4
# The controller setup sends ~10 commands; the real pump takes ~4 s for them.
PUMP_SETUP_TIMEOUT_S = 15

pump = None
mode = "mock"                       # "real" or "mock"
_address: Optional[str] = None      # serial port of the real pump, None for the mock
_message = "Pump not detected yet"
_logging = False
_hw = None                          # communicator of the real pump
controller: Optional[DilutionController] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_step: Optional[concurrent.futures.Future] = None
_lock = threading.Lock()
_connect_lock = threading.RLock()


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


def set_logging(on: bool) -> None:
    """Turn the ismatec library's debug logging on or off (to the terminal)."""
    global _logging
    _logging = bool(on)
    _ismatec_log.setLevel(logging.DEBUG if _logging else logging.WARNING)


def info() -> dict:
    """What `connect` last found, without reconnecting."""
    return {"mode": mode, "address": _address, "message": _message, "logging": _logging}


def _abort(hw) -> None:
    """Stop a communicator whose pump never answered: end its thread (which closes
    the port) and unblock anything waiting for a reply."""
    if hw is None:
        return
    try:
        hw._stop_event.set()
        for _ in range(20):
            hw.res_q.put('')
    except Exception:
        pass


def _build_real(holder: list, result: concurrent.futures.Future) -> None:
    """Worker thread: construct the real Pump. Its constructor queries the pump
    with no timeout, so this may never return; `holder` gets the communicator so
    the caller can abort it."""
    class Probe(RealPump):
        def __setattr__(self, name, value):
            if name == "hw":
                holder.append(value)
            super().__setattr__(name, value)
    try:
        result.set_result(Probe(_address_to_try, debug=False))
    except BaseException as e:
        result.set_exception(e)


_address_to_try = PUMP_ADDRESS


def usb_ports() -> List[dict]:
    """Every USB serial port, sorted, as {"device", "description"} (skipping
    macOS' /dev/tty.* twin of each /dev/cu.* port). For the port dropdowns."""
    found = [p for p in list_ports.comports()
             if p.vid is not None and not p.device.startswith("/dev/tty.")]
    return [{"device": p.device, "description": p.description or ""}
            for p in sorted(found, key=lambda p: p.device)]


def _candidates(address: Optional[str] = None) -> List[str]:
    """The ports the pump may be on: `address` or PUMP_ADDRESS if set, else
    every USB serial port that isn't a known board."""
    if address or PUMP_ADDRESS is not None:
        return [address or PUMP_ADDRESS]
    return sorted(p.device for p in list_ports.comports()
                  if p.vid is not None and p.vid not in NOT_PUMP_VIDS
                  and not p.device.startswith("/dev/tty."))


def _real_port_missing(address: str) -> bool:
    # Only serial device paths can be checked; COMx and host:port are tried as-is.
    # (The rotator's Nano isn't refused by USB ID here: the pump's USB chip may be
    # a Nano-like one too. Api.pump_connect refuses the rotator's open port.)
    return address.startswith("/dev") and not os.path.exists(address)


def _answers_like_pump(address: str) -> bool:
    """True if `address` answers the pump's version query ('1#') within a second.
    Needed because the library doesn't fail on a port that never answers (e.g. a
    USB-C dock's): its queries just come back empty. Always closes the port."""
    try:
        with serial.Serial(address, 9600, timeout=1) as ser:
            ser.reset_input_buffer()
            ser.write(b"1#\r")
            return bool(ser.readline().strip())
    except (serial.SerialException, OSError):
        return False


def _try_real(address: Optional[str] = None) -> bool:
    """Connect to the real pump on the first candidate port that answers. False
    if there is none. A port chosen by hand is probed too, so a wrong one (e.g.
    a dock's) isn't taken for the pump."""
    return any(not _real_port_missing(a) and _answers_like_pump(a) and _try_real_at(a)
               for a in _candidates(address))


def _try_real_at(address: str) -> bool:
    """Connect to the real pump at `address` and set the controller up. False if
    it isn't there."""
    global pump, controller, mode, _address, _message, _hw, _address_to_try
    if _real_port_missing(address):
        return False
    _address_to_try = address
    holder: list = []
    built: concurrent.futures.Future = concurrent.futures.Future()
    threading.Thread(target=_build_real, args=(holder, built), daemon=True).start()
    try:
        new_pump = built.result(PUMP_TIMEOUT_S)
    except BaseException as e:
        logging.getLogger(__name__).warning("Real pump on %s not usable: %r", address, e)
        for hw in holder:
            _abort(hw)
        return False
    hw = holder[0] if holder else getattr(new_pump, "hw", None)
    new_controller = DilutionController(new_pump)
    try:
        _submit(new_controller.setup()).result(PUMP_SETUP_TIMEOUT_S)
    except BaseException as e:
        logging.getLogger(__name__).warning("Pump on %s failed setup: %r", address, e)
        _abort(hw)
        return False
    pump, controller, _hw = new_pump, new_controller, hw
    mode, _address, _message = "real", address, f"Pump connected on {address}"
    return True


async def _make_mock(address: Optional[str] = None) -> None:
    global pump, controller, mode, _address, _message, _hw
    # Create inside the running loop (the mock grabs the current loop).
    new_pump = MockPump(PUMP_ADDRESS or "mock", debug=False)
    new_controller = DilutionController(new_pump)
    await new_controller.setup()
    pump, controller, _hw = new_pump, new_controller, None
    mode, _address = "mock", None
    if FORCE_MOCK:
        _message = "Using the mock pump"
    else:
        tried = ", ".join(_candidates(address))
        where = f"on {tried}" if tried else "(no USB serial ports)"
        _message = f"No pump found {where} - using the mock pump"


async def _drop_pump() -> None:
    """Stop the current pump's channels and close its port."""
    global pump, controller, _hw
    old, hw = pump, _hw
    pump = controller = _hw = None
    if old is not None:
        try:
            for ch in old.channels:
                await old.stop(ch)
        except Exception:
            pass
    if hw is not None:
        _abort(hw)
        try:
            hw.join(2)
        except Exception:
            pass


def connect(address: Optional[str] = None) -> dict:
    """(Re)detect the pump: the real one on `address` (a port chosen in the UI;
    None: see _candidates), else the mock. Drops the old pump first. Blocks the
    calling thread (not the asyncio thread) for at most a few PUMP_TIMEOUT_S;
    never hangs. Returns `info()`."""
    with _connect_lock:
        try:
            _submit(_drop_pump()).result(PUMP_TIMEOUT_S)
        except Exception:
            pass
        if FORCE_MOCK or not _try_real(address):
            _submit(_make_mock(address)).result(PUMP_TIMEOUT_S + 5)
        print(f"[pump] {_message}")
        return info()


async def _init_hardware() -> None:
    if controller is None:
        # connect() blocks, so keep it off the asyncio thread.
        await asyncio.to_thread(connect)


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
    # Sent here, not via the library's set_tubing_inner_diameter: its
    # pack_discrete2 drops trailing zeros (2.1 -> '0021' = 0.21 mm).
    hundredths = round(mm * 100)
    for ch in CHANNELS.values():
        if not pump.hw.command(f"{ch}+{hundredths:04d}"):
            raise RuntimeError(f"Pump refused tubing {mm} mm on channel {ch}")
    return await _get_tubing()


def get_tubing() -> Dict[str, Dict[str, float]]:
    """Tubing inner diameter (mm) and calibrated max flow (mL/min) of each
    channel A-D, as stored on the pump."""
    return _submit(_get_tubing()).result(TUBING_TIMEOUT_S)


def set_tubing(mm: float) -> Dict[str, Dict[str, float]]:
    """Set the tubing inner diameter of every channel to `mm` (TUBING_MIN_MM..TUBING_MAX_MM), then return `get_tubing()`. Don't call it while a step runs."""
    return _submit(_set_tubing(mm)).result(TUBING_TIMEOUT_S)
