"""
Tests for the cuvette rotator: the serial driver (gui/rotator.py) against a fake
board that behaves like cuvette_rotater/cuvette_rotater.ino, and the Api's rotate
block, manual rotate and connect.
"""

import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import api
import rotator
from test_api import FakeWindow


class FakeBoard:
    """Fake serial port speaking the firmware protocol. Replies arrive a few bytes
    at a time; `stale` is output still in the pipe from before we connected.
    Like the Nano, it says BOOTED when opened (`booted=False`: a board that
    didn't restart, which only answers PING)."""

    def __init__(self, stale: bytes = b"", move_delay: float = 0.0, booted: bool = True):
        self.out = bytearray(stale)
        if booted:
            self.out += b"<STATUS:BOOTED>\r\n"
        self.written: List[str] = []
        self.move_delay = move_delay
        self.lock = threading.Lock()
        self.closed = False

    @property
    def in_waiting(self) -> int:
        with self.lock:
            return min(len(self.out), 5)

    def read(self, n: int) -> bytes:
        with self.lock:
            n = min(n, 5, len(self.out))
            data, self.out[:] = bytes(self.out[:n]), self.out[n:]
        if not data:
            time.sleep(0.01)
        return data

    def _say(self, line: str) -> None:
        with self.lock:
            self.out += (line + "\r\n").encode()

    def write(self, data: bytes) -> None:
        cmd = data.decode().strip()
        if cmd == "PING":
            self._say("<STATUS:PONG>")
            return
        self.written.append(cmd)
        if cmd == "HOME":
            self._say("<STATUS:HOMING>")
            self._say("<STATUS:SEARCHING_FOR_ZERO>")
            self._say("<STATUS:READY>")
        elif cmd.isdigit():
            deg = float(cmd)
            if not 0 <= deg <= 60:
                self._say("<ERROR:OUT_OF_BOUNDS>")
                return
            self._say(f"<STATUS:MOVING_TO_{deg}>")
            if self.move_delay:
                threading.Timer(self.move_delay, self._say, ["<STATUS:MOVE_COMPLETE>"]).start()
            else:
                self._say("<STATUS:MOVE_COMPLETE>")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def board(monkeypatch):
    """A fake Nano (CH340) on /dev/fake; returns the FakeBoard that connect() opens."""
    state = SimpleNamespace(board=FakeBoard())
    monkeypatch.setattr(
        rotator.list_ports, "comports",
        lambda: [SimpleNamespace(device="/dev/other", vid=0x05AC, pid=0x1234, product="Bluetooth"),
                 SimpleNamespace(device="/dev/fake", vid=0x1A86, pid=0x7523, product="USB Serial")],
    )
    monkeypatch.setattr(rotator.serial, "Serial", lambda *a, **k: state.board)
    return state


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("deg, ok", [(0, True), (60, True), (30.0, True), ("45", True), (61, False), (-1, False), (30.5, False), (None, False)])
def test_rotate_error(deg, ok):
    assert (rotator.rotate_error(deg) is None) == ok


def test_find_port_by_vid_pid(board):
    assert rotator.find_port() == "/dev/fake"


def test_find_port_none(monkeypatch):
    monkeypatch.setattr(rotator.list_ports, "comports", lambda: [])
    assert rotator.find_port() is None


def test_connect_skips_silent_port(monkeypatch):
    # Two Arduino-like ports: the first never answers (e.g. the pump), the second is the rotator
    monkeypatch.setattr(rotator, "BOOT_TIMEOUT_S", 0.1)
    monkeypatch.setattr(rotator, "PING_TIMEOUT_S", 0.1)
    monkeypatch.setattr(
        rotator.list_ports, "comports",
        lambda: [SimpleNamespace(device="/dev/pump", vid=0x0403, pid=0x6001, product="FT232R"),
                 SimpleNamespace(device="/dev/nano", vid=0x1A86, pid=0x7523, product="USB Serial")],
    )
    silent, nano = FakeBoard(booted=False), FakeBoard()
    silent.write = lambda data: None
    monkeypatch.setattr(rotator.serial, "Serial", lambda port, *a, **k: silent if port == "/dev/pump" else nano)
    r = rotator.Rotator()
    assert r.connect() == "/dev/nano"
    assert silent.closed and nano.written == ["HOME"]


def test_connect_pings_board_that_did_not_restart(board, monkeypatch):
    monkeypatch.setattr(rotator, "BOOT_TIMEOUT_S", 0.1)
    board.board = FakeBoard(booted=False)
    assert rotator.Rotator().connect() == "/dev/fake"


def test_find_port_ignores_other_devices(monkeypatch):
    monkeypatch.setattr(rotator.list_ports, "comports",
                        lambda: [SimpleNamespace(device="/dev/bt", vid=None, pid=None, product=None)])
    assert rotator.find_port() is None


def test_find_port_empty(monkeypatch):
    monkeypatch.setattr(rotator.list_ports, "comports", lambda: [])
    assert rotator.find_port() is None


class TestDriver:
    def test_connect_homes(self, board):
        r = rotator.Rotator()
        assert r.connect() == "/dev/fake"
        assert board.board.written == ["HOME"]
        assert r.deg == 0

    def test_move_sends_whole_degrees(self, board):
        r = rotator.Rotator()
        r.connect()
        r.move(30.0)
        assert board.board.written[-1] == "30"
        assert r.deg == 30

    def test_stale_replies_are_skipped(self, board):
        # A MOVE_COMPLETE and READY left over from earlier must not end our move early
        board.board = FakeBoard(stale=b"<STATUS:READY>\r\n<STATUS:MOVE_COMPLETE>\r\n", move_delay=0.2)
        r = rotator.Rotator()
        r.connect()
        t0 = time.monotonic()
        r.move(20)
        assert time.monotonic() - t0 >= 0.15
        assert r.deg == 20

    def test_cancel_stops_waiting(self, board):
        board.board = FakeBoard(move_delay=5)
        r = rotator.Rotator()
        r.connect()
        t0 = time.monotonic()
        r.move(40, should_cancel=lambda: time.monotonic() - t0 > 0.1)
        assert time.monotonic() - t0 < 1
        assert r.deg == 40  # the board still gets there

    def test_move_refuses_bad_angle(self, board):
        r = rotator.Rotator()
        r.connect()
        with pytest.raises(rotator.RotatorError):
            r.move(61)
        assert board.board.written == ["HOME"]

    def test_move_without_connect(self):
        with pytest.raises(rotator.RotatorError):
            rotator.Rotator().move(10)

    def test_connect_without_board(self, monkeypatch):
        monkeypatch.setattr(rotator.list_ports, "comports", lambda: [])
        with pytest.raises(rotator.RotatorError, match="not found"):
            rotator.Rotator().connect()


@pytest.fixture
def window():
    return FakeWindow()


@pytest.fixture
def connected_api(board, window):
    a = api.Api()
    a.set_window(window)
    assert a.rotator_connect() == {"ok": True}
    assert wait_for(lambda: a._rotator_state == "ready")
    return a


def rotator_events(window) -> List[Dict[str, Any]]:
    return [e for e in window.events if e["type"] == "rotator"]


class TestApi:
    def test_connect_emits_homing_then_ready(self, connected_api, window):
        states = [(e["state"], e["deg"]) for e in rotator_events(window)]
        assert states == [("homing", None), ("ready", 0)]

    def test_connect_without_board(self, monkeypatch, window):
        monkeypatch.setattr(rotator.list_ports, "comports", lambda: [])
        a = api.Api()
        a.set_window(window)
        a.rotator_connect()
        assert wait_for(lambda: rotator_events(window)[-1]["state"] == "disconnected")
        assert "not found" in rotator_events(window)[-1]["message"]

    def test_program_with_rotate_block(self, connected_api, window):
        blocks = [{"id": "r1", "type": "rotate", "deg": 45}, {"id": "p1", "type": "pause", "s": 0.05}]
        assert connected_api.run_program(blocks, 1) == {"ok": True}
        assert wait_for(lambda: any(e == {"type": "status", "status": "done"} for e in window.events))
        moving = [e for e in rotator_events(window) if e["state"] == "moving"]
        assert moving == [{"type": "rotator", "state": "moving", "deg": 0, "target": 45}]
        assert rotator_events(window)[-1] == {"type": "rotator", "state": "ready", "deg": 45}

    def test_program_refused_without_rotator(self, window):
        a = api.Api()
        a.set_window(window)
        result = a.run_program([{"id": "r1", "type": "rotate", "deg": 10}], 1)
        assert result == {"ok": False, "error": "Cuvette rotator not connected"}

    def test_program_refuses_bad_angle(self, connected_api):
        result = connected_api.run_program([{"id": "r1", "type": "rotate", "deg": 75}], 1)
        assert result["ok"] is False

    def test_program_refuses_unknown_block(self, connected_api):
        assert connected_api.run_program([{"id": "x", "type": "spin"}], 1)["ok"] is False

    def test_manual_rotate_homes_first(self, board, connected_api, window):
        window.events.clear()
        assert connected_api.manual_rotate(20) == {"ok": True}
        assert wait_for(lambda: window.events[-1] == {"type": "status", "status": "ready"})
        statuses = [e["status"] for e in window.events if e["type"] == "status"]
        assert statuses == ["rotating", "ready"]
        # every click re-grounds on the 0° switch, then moves
        assert board.board.written == ["HOME", "HOME", "20"]
        assert [(e["state"], e["deg"]) for e in rotator_events(window)] == [
            ("homing", 0), ("moving", 0), ("ready", 20)]  # homing reports the last known angle
        assert connected_api._rotator.deg == 20

    def test_program_rotate_does_not_home(self, board, connected_api, window):
        connected_api.run_program([{"id": "r1", "type": "rotate", "deg": 10}], 1)
        assert wait_for(lambda: any(e == {"type": "status", "status": "done"} for e in window.events))
        assert board.board.written == ["HOME", "10"]

    def test_manual_rotate_refused_while_busy(self, board, connected_api):
        board.board.move_delay = 0.5
        assert connected_api.manual_rotate(10)["ok"] is True
        assert connected_api.manual_rotate(20) == {"ok": False, "error": "Cuvette rotator is busy"}

    def test_stop_during_rotate(self, board, connected_api, window):
        board.board.move_delay = 5
        connected_api.run_program([{"id": "r1", "type": "rotate", "deg": 50}], 1)
        assert wait_for(lambda: connected_api._rotator_state == "moving")
        t0 = time.monotonic()
        connected_api.stop()
        assert wait_for(lambda: not connected_api._busy(), timeout=1)
        assert time.monotonic() - t0 < 1
