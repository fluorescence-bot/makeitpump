"""
Tests for gui/hardware.py against the `ismatec` library's mock pump
(hardware.FORCE_MOCK = True). The mock pumps one channel at a time, about
2 s per mL, so volumes are kept small.
"""

import logging
import queue
import threading
import time
import types

import pytest

import hardware



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


def test_set_tubing_sets_every_channel():
    tubing = hardware.set_tubing(0.19)
    assert tubing["mm"] == {"A": 0.19, "B": 0.19, "C": 0.19, "D": 0.19}
    assert tubing["max_ml_per_min"]["A"] > 0
    assert hardware.get_tubing() == tubing


def test_set_tubing_accepts_trailing_zero_value():
    """2.1 must be sent as '0210', not the library's '0021'."""
    tubing = hardware.set_tubing(2.1)
    assert tubing["mm"] == {"A": 2.1, "B": 2.1, "C": 2.1, "D": 2.1}


def test_connect_force_mock():
    info = hardware.connect()
    assert info["mode"] == "mock" and info["address"] is None
    assert info == hardware.info()


def test_reconnect_then_pump(monkeypatch):
    hardware.connect()
    first = hardware.pump
    hardware.connect()
    assert hardware.pump is not first
    hardware.run_step({"A": 0.1})
    assert not any(hardware.pump.running.values())


def test_missing_port_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(hardware, "FORCE_MOCK", False)
    monkeypatch.setattr(hardware, "PUMP_ADDRESS", "/dev/does-not-exist-pump")
    info = hardware.connect()
    assert info["mode"] == "mock"
    assert info["message"] == "No pump found on /dev/does-not-exist-pump - using the mock pump"


@pytest.mark.timeout(8)
def test_rotator_port_is_never_taken_for_the_pump(monkeypatch, tmp_path):
    """Without the pump, the rotator board can show up on PUMP_ADDRESS."""
    port = tmp_path / "ttyACM0"
    port.touch()
    monkeypatch.setattr(hardware, "FORCE_MOCK", False)
    monkeypatch.setattr(hardware, "PUMP_ADDRESS", str(port))
    monkeypatch.setattr(hardware.rotator, "find_port", lambda: str(port))
    info = hardware.connect()
    assert info["mode"] == "mock"
    assert str(port) in info["message"]


def test_auto_detect_skips_boards_and_ports_that_dont_answer(monkeypatch):
    def port(device, vid):
        return types.SimpleNamespace(device=device, vid=vid)
    ports = [port("COM1", None), port("COM3", 0x057E), port("COM4", 0x1A86),
             port("COM5", 0x239A), port("COM6", 0x1234)]
    monkeypatch.setattr(hardware, "FORCE_MOCK", False)
    monkeypatch.setattr(hardware, "PUMP_ADDRESS", None)
    monkeypatch.setattr(hardware.list_ports, "comports", lambda: ports)
    assert hardware._candidates() == ["COM3", "COM6"]
    probed = []
    monkeypatch.setattr(hardware, "_answers_like_pump",
                        lambda a: probed.append(a) or a == "COM6")
    tried = []
    monkeypatch.setattr(hardware, "_try_real_at", lambda a: tried.append(a) or True)
    assert hardware._try_real()
    assert probed == ["COM3", "COM6"] and tried == ["COM6"]


def test_detection_does_not_hang_on_silent_pump(monkeypatch, tmp_path):
    port = tmp_path / "ttyFAKE"
    port.write_text("")
    release = threading.Event()

    class Silent:
        def __init__(self, *a, **k):
            self.hw = types.SimpleNamespace(_stop_event=threading.Event(), res_q=queue.Queue())
            release.wait(30)  # never answers

    monkeypatch.setattr(hardware, "FORCE_MOCK", False)
    monkeypatch.setattr(hardware, "PUMP_ADDRESS", str(port))
    monkeypatch.setattr(hardware, "_real_port_missing", lambda a: False)
    monkeypatch.setattr(hardware, "RealPump", Silent)
    monkeypatch.setattr(hardware, "PUMP_TIMEOUT_S", 0.5)
    start = time.monotonic()
    info = hardware.connect()
    release.set()
    assert time.monotonic() - start < 5
    assert info["mode"] == "mock"
    assert info["message"].startswith("No pump found on")


def test_set_logging_changes_ismatec_level(monkeypatch):
    monkeypatch.setattr(hardware, "_logging", False)
    hardware.set_logging(True)
    assert logging.getLogger("ismatec").level == logging.DEBUG
    assert hardware.info()["logging"] is True
    hardware.set_logging(False)
    assert logging.getLogger("ismatec").level == logging.WARNING
