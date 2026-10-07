"""
Tests for gui/hardware.py against the `ismatec` library's mock pump
(hardware.DEBUGGING = True). The mock pumps one channel at a time, about
2 s per mL, so volumes are kept small.
"""

import threading
import time

import hardware


def test_mock_is_used():
    assert hardware.DEBUGGING


def test_run_step_pumps_and_returns():
    start = time.monotonic()
    hardware.run_step({"A": 0.1, "D": 0.1})
    assert time.monotonic() - start < 5
    assert not any(hardware.pump.running.values())


def test_run_step_with_nothing_to_pump_returns_at_once():
    start = time.monotonic()
    hardware.run_step({"A": 0, "B": 0})
    assert time.monotonic() - start < 0.1


def test_stop_all_ends_run_step():
    """Stop returns the runner at once (the mock itself finishes the channel it is on)."""
    threading.Timer(0.2, hardware.stop_all).start()
    start = time.monotonic()
    hardware.run_step({"A": 1.0})
    assert time.monotonic() - start < 1.0
