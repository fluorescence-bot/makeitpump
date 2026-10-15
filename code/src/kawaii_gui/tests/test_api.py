"""
Tests for RainMixer API (gui/api.py).

These tests verify:
- Multi-channel pump block execution and event ordering
- Stop behavior and concurrent run prevention
- Manual dosing (fixed mL per click, rejected while busy, partial on stop)
- `pumped` events for the session log
- Preset round-trip (save, list, load)
- Repeat logic
"""

import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, List
import pytest
import api
import hardware

FAKE_PUMP_ML_PER_S = 10.0  # the fake pump's own rate


class FakeWindow:
    """Fake pywebview window for testing."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def evaluate_js(self, js_code: str) -> None:
        """Parse window.__onBackendEvent(...) calls and append to events."""
        # js_code looks like: "window.__onBackendEvent({...})"
        if "__onBackendEvent(" in js_code:
            json_str = js_code.split("__onBackendEvent(", 1)[1].rsplit(")", 1)[0]
            event = json.loads(json_str)
            self.events.append(event)


@pytest.fixture
def fake_window():
    """Fixture providing a fake window."""
    return FakeWindow()


@pytest.fixture
def api_instance(fake_window):
    """Fixture providing an initialized Api instance."""
    instance = api.Api()
    instance.set_window(fake_window)
    return instance


@pytest.fixture(autouse=True)
def fast_flow(monkeypatch):
    """Estimate the fake pump's flow (FAKE_PUMP_ML_PER_S), so tests stay fast."""
    monkeypatch.setattr(hardware, "FLOW_ML_PER_S", FAKE_PUMP_ML_PER_S)


def test_pump_seconds_uses_flow():
    assert api.pump_seconds(2) == pytest.approx(0.2)


@pytest.mark.parametrize(
    "pumps, error",
    [
        ({"A": {"ml": 1}}, None),
        ({"A": {"ml": 0}}, None),
        ({"A": {"ml": 1, "speed": 120}}, None),  # old presets' speed is ignored
        ({"C": {"ml": -1}}, "C: negative mL"),
    ],
)
def test_pumps_error(pumps, error):
    assert api.pumps_error(pumps) == error


def test_run_rejects_negative_ml(api_instance, mock_hardware):
    blocks = [{"id": "b1", "type": "pump", "pumps": {"A": {"ml": -1}}}]
    result = api_instance.run_program(blocks, 1)
    assert result == {"ok": False, "error": "A: negative mL"}
    assert mock_hardware["run_step"] == []


@pytest.fixture
def mock_hardware(monkeypatch):
    """Fixture to mock and spy on hardware calls. The fake `run_step` blocks
    until its largest volume is pumped at FAKE_PUMP_ML_PER_S, or `stop_all`."""
    calls = {"run_step": [], "stop_all": []}
    stopped = threading.Event()

    def fake_run_step(volumes: Dict[str, float]) -> None:
        calls["run_step"].append(dict(volumes))
        stopped.clear()
        stopped.wait(max(volumes.values()) / FAKE_PUMP_ML_PER_S)

    def fake_stop_all() -> None:
        calls["stop_all"].append(None)
        stopped.set()

    monkeypatch.setattr(hardware, "run_step", fake_run_step)
    monkeypatch.setattr(hardware, "stop_all", fake_stop_all)

    return calls


def wait_for_thread(thread: threading.Thread, timeout: float = 2.0) -> bool:
    """Wait for a thread to finish. Returns True if joined, False if timeout."""
    thread.join(timeout=timeout)
    return not thread.is_alive()


def wait_for_status(window: FakeWindow, status: str, timeout: float = 1.0) -> bool:
    """Poll until a status event is emitted. Returns True if seen before timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e["type"] == "status" and e["status"] == status for e in window.events):
            return True
        time.sleep(0.01)
    return False


class TestPumpBlock:
    """Tests for pump block execution."""

    def test_pump_block_multi_channel(self, api_instance, fake_window, mock_hardware):
        """Test multi-channel pump block with channels A and B active."""
        # A: 9 mL at 30% (0.3 s), B: 5 mL at 50% (0.1 s), C: idle, D: idle
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 9},
                    "B": {"ml": 5},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True

        # Wait for runner thread to finish
        assert wait_for_thread(api_instance._runner_thread)

        # Verify events
        events = fake_window.events
        assert len(events) > 0

        # First event should be status: raining
        assert events[0]["type"] == "status"
        assert events[0]["status"] == "raining"

        # Should have block_start
        block_starts = [e for e in events if e["type"] == "block_start"]
        assert len(block_starts) == 1
        assert block_starts[0]["index"] == 0
        assert block_starts[0]["repeat"] == 0

        # Should have block_end
        block_ends = [e for e in events if e["type"] == "block_end"]
        assert len(block_ends) == 1

        # Should have progress event
        progress = [e for e in events if e["type"] == "progress"]
        assert len(progress) > 0

        # Should end with status: done
        final_events = [e for e in events if e["type"] == "status" and e["status"] == "done"]
        assert len(final_events) == 1

        # One step with only A and B (ml > 0)
        assert mock_hardware["run_step"] == [{"A": 9, "B": 5}]

    def test_pump_block_idle_channels_dont_start(self, api_instance, mock_hardware):
        """Channels with no mL should not be pumped."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 0},  # no mL, should not start
                    "B": {"ml": 5},  # active
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread)

        # Only B should be pumped
        assert mock_hardware["run_step"] == [{"B": 5}]

    def test_pump_block_duration(self, api_instance, fake_window, mock_hardware):
        """The block lasts until the pump has done every channel."""
        start_time = time.time()
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 2.5},
                    "B": {"ml": 1.5},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread)
        duration = time.time() - start_time

        # Block should last ~0.25s (max of A and B)
        assert duration >= 0.2  # Allow some margin
        assert duration < 0.5


class TestStop:
    """Tests for stop functionality."""

    def test_stop_halts_pump(self, api_instance, fake_window, mock_hardware):
        """Stop should halt pumps quickly and emit stopped status."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 100},  # Long run
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]
        api_instance.run_program(blocks, 1)
        time.sleep(0.1)  # Let it start

        # Call stop
        result = api_instance.stop()
        assert result["ok"] is True

        # Wait for thread to die
        assert wait_for_thread(api_instance._runner_thread, timeout=1.0)

        # Check for stopped status
        events = fake_window.events
        stopped = [e for e in events if e["type"] == "status" and e["status"] == "stopped"]
        assert len(stopped) >= 1

        # Verify stop_all was called
        assert api_instance._stop_event.is_set()

    def test_stop_allows_new_run(self, api_instance, mock_hardware):
        """After stop, a new run_program should be allowed."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 3},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]

        # First run
        api_instance.run_program(blocks, 1)
        time.sleep(0.05)
        api_instance.stop()
        # Wait for runner thread to die
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        # Small delay to ensure lock is released
        time.sleep(0.05)

        # Second run should succeed
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_concurrent_run_rejected(self, api_instance, mock_hardware):
        """run_program should reject if one is already running."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]

        result1 = api_instance.run_program(blocks, 1)
        assert result1["ok"] is True

        # Try to run again immediately (while first is still running)
        time.sleep(0.01)  # Ensure the thread has started
        result2 = api_instance.run_program(blocks, 1)
        assert result2["ok"] is False
        assert "already running" in result2.get("error", "").lower()

        # Clean up
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)


class TestPauseResume:
    """Tests for pause and resume functionality."""

    TWO_BLOCKS = [
        {"id": "b1", "type": "pump", "pumps": {"A": {"ml": 4}}},  # 0.4 s
        {"id": "b2", "type": "pump", "pumps": {"B": {"ml": 1}}},  # 0.1 s
    ]

    def test_pause_waits_for_block_end(self, api_instance, fake_window, mock_hardware):
        """A pump block can't pause: the pause takes effect before the next block."""
        assert api_instance.run_program(self.TWO_BLOCKS, 1)["ok"] is True
        time.sleep(0.1)

        assert api_instance.pause_run()["ok"] is True
        # The first block keeps pumping; no pause yet
        assert not wait_for_status(fake_window, "paused", timeout=0.15)
        assert wait_for_status(fake_window, "paused", timeout=1.0)
        assert mock_hardware["run_step"] == [{"A": 4}]
        assert [e["index"] for e in fake_window.events if e["type"] == "block_end"] == [0]

        time.sleep(0.1)
        assert mock_hardware["run_step"] == [{"A": 4}]
        assert api_instance.resume_run()["ok"] is True
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        assert mock_hardware["run_step"] == [{"A": 4}, {"B": 1}]
        statuses = [e["status"] for e in fake_window.events if e["type"] == "status"]
        assert statuses == ["raining", "paused", "raining", "done"]

    def test_pause_and_resume_within_block_is_moot(self, api_instance, fake_window, mock_hardware):
        """Pausing and resuming before the block ends doesn't pause anything."""
        api_instance.run_program(self.TWO_BLOCKS, 1)
        time.sleep(0.1)
        api_instance.pause_run()
        api_instance.resume_run()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        statuses = [e["status"] for e in fake_window.events if e["type"] == "status"]
        assert statuses == ["raining", "done"]
        assert not api_instance._resume_event.is_set()

    def test_pause_then_stop_within_block(self, api_instance, fake_window, mock_hardware):
        """Stop after a pending pause ends on `stopped`, never `paused`."""
        api_instance.run_program(self.TWO_BLOCKS, 1)
        time.sleep(0.1)
        api_instance.pause_run()
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        statuses = [e["status"] for e in fake_window.events if e["type"] == "status"]
        assert statuses == ["raining", "stopped"]
        assert mock_hardware["run_step"] == [{"A": 4}]

    def test_stop_while_paused(self, api_instance, fake_window, mock_hardware):
        """Stop during a pause between blocks ends the run without pumping more."""
        api_instance.run_program(self.TWO_BLOCKS, 1)
        time.sleep(0.1)
        api_instance.pause_run()
        assert wait_for_status(fake_window, "paused", timeout=1.0)
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)
        assert mock_hardware["run_step"] == [{"A": 4}]


class TestManualDose:
    """Tests for manual dosing (one click = fixed mL)."""

    def test_dose_pumps_fixed_amount(self, api_instance, fake_window, mock_hardware):
        """A dose pumps the given mL on its channel and reports the full mL."""
        result = api_instance.manual_dose("A", 2.5)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread)

        assert mock_hardware["run_step"] == [{"A": 2.5}]

        assert fake_window.events[0] == {"type": "status", "status": "dosing"}
        assert {"type": "dose_start", "channel": "A", "ml": 2.5} in fake_window.events

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert pumped == [{"type": "pumped", "channel": "A", "ml": 2.5, "source": "manual"}]
        assert fake_window.events[-1] == {"type": "status", "status": "ready"}

    def test_dose_rejected_while_dosing(self, api_instance, mock_hardware):
        """A second click while a dose runs is refused."""
        assert api_instance.manual_dose("A", 5)["ok"] is True
        time.sleep(0.05)

        result = api_instance.manual_dose("B", 1)
        assert result["ok"] is False
        assert "Busy" in result.get("error", "")

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_dose_rejected_during_run(self, api_instance, mock_hardware):
        """A dose is refused while a program is running."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 5},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]

        api_instance.run_program(blocks, 1)
        time.sleep(0.05)  # Let it start

        result = api_instance.manual_dose("B", 1)
        assert result["ok"] is False
        assert "Busy" in result.get("error", "")

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_run_rejected_during_dose(self, api_instance, mock_hardware):
        """A program cannot start while a dose runs."""
        api_instance.manual_dose("A", 5)
        time.sleep(0.05)

        result = api_instance.run_program([{"id": "b1", "type": "pause", "s": 0.1}], 1)
        assert result["ok"] is False

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_stopped_dose_reports_partial_ml(self, api_instance, fake_window, mock_hardware):
        """Stopping mid-dose logs only what was actually pumped."""
        api_instance.manual_dose("C", 10)
        time.sleep(0.3)
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert len(pumped) == 1
        assert pumped[0]["channel"] == "C"
        assert pumped[0]["source"] == "manual"
        assert 1.0 < pumped[0]["ml"] < 6.0

    @pytest.mark.parametrize("ml", [0, -1])
    def test_dose_rejects_bad_values(self, api_instance, mock_hardware, ml):
        """Zero or negative mL is refused without touching the pump."""
        assert api_instance.manual_dose("A", ml)["ok"] is False
        assert mock_hardware["run_step"] == []


class TestPumpedEvents:
    """Program blocks also report what they pumped, for the session log."""

    def test_program_reports_pumped_per_channel(self, api_instance, fake_window, mock_hardware):
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 3},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 1},
                },
            }
        ]
        api_instance.run_program(blocks, 2)
        assert wait_for_thread(api_instance._runner_thread)

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert all(e["source"] == "program" for e in pumped)
        assert sum(e["ml"] for e in pumped if e["channel"] == "A") == pytest.approx(6)
        assert sum(e["ml"] for e in pumped if e["channel"] == "D") == pytest.approx(2)
        assert not any(e["channel"] in ("B", "C") for e in pumped)

    def test_stopped_program_reports_partial(self, api_instance, fake_window, mock_hardware):
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 10},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]
        api_instance.run_program(blocks, 1)
        time.sleep(0.3)
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert len(pumped) == 1
        assert 1.0 < pumped[0]["ml"] < 6.0


class TestRepeat:
    """Tests for repeat functionality."""

    def test_repeat_runs_multiple_times(self, api_instance, fake_window, mock_hardware):
        """Test that repeat > 1 runs the block sequence multiple times."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1},
                    "B": {"ml": 0},
                    "C": {"ml": 0},
                    "D": {"ml": 0},
                },
            }
        ]

        result = api_instance.run_program(blocks, 3)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        # Count block_start events
        block_starts = [e for e in fake_window.events if e["type"] == "block_start"]
        # Should have 3 starts (one per repeat)
        assert len(block_starts) == 3

        # Verify repeat indices
        repeats = [e["repeat"] for e in block_starts]
        assert repeats == [0, 1, 2]


class TestPresets:
    """Tests for preset save/load/list functionality."""

    def test_preset_round_trip(self, api_instance, tmp_path, monkeypatch):
        """Test save -> list -> load preserves preset data."""
        # Monkeypatch to use tmp_path
        monkeypatch.setattr(api.Api, "_get_presets_dir", lambda self: tmp_path)

        preset = {
            "name": "Test Preset",
            "solutions": {"A": "Liquid A", "B": "Liquid B", "C": "Liquid C", "D": "Result"},
            "blocks": [
                {
                    "id": "b1",
                    "type": "pump",
                    "pumps": {
                        "A": {"ml": 10},
                        "B": {"ml": 5},
                        "C": {"ml": 0},
                        "D": {"ml": 0},
                    },
                }
            ],
            "repeat": 2,
        }

        # Save
        save_result = api_instance.save_preset(preset)
        assert save_result["ok"] is True

        # List
        listed = api_instance.list_presets()
        assert "Test Preset" in listed

        # Load
        loaded = api_instance.load_preset("Test Preset")
        assert loaded is not None
        assert loaded["name"] == preset["name"]
        assert loaded["solutions"] == preset["solutions"]
        assert loaded["repeat"] == preset["repeat"]
        assert len(loaded["blocks"]) == len(preset["blocks"])

    def test_sanitize_filename(self, api_instance, tmp_path, monkeypatch):
        """Test that sanitize_filename prevents directory traversal."""
        monkeypatch.setattr(api.Api, "_get_presets_dir", lambda self: tmp_path)

        dangerous_names = [
            "preset/../../../etc/passwd",
            "preset/../../secret",
            "preset<>|",
            "preset:with:colons",
        ]

        for name in dangerous_names:
            preset = {
                "name": name,
                "solutions": {"A": "", "B": "", "C": "", "D": ""},
                "blocks": [],
                "repeat": 1,
            }

            result = api_instance.save_preset(preset)
            assert result["ok"] is True

            # Verify the file was saved in presets_dir, not elsewhere
            saved_files = list(tmp_path.glob("*.yaml"))
            assert len(saved_files) > 0

            # Verify file path is under tmp_path
            for f in saved_files:
                assert f.resolve().parent == tmp_path.resolve()

    def test_list_presets_empty(self, api_instance, tmp_path, monkeypatch):
        """Test list_presets returns empty list for new directory."""
        monkeypatch.setattr(api.Api, "_get_presets_dir", lambda self: tmp_path)

        listed = api_instance.list_presets()
        assert listed == []


class TestPauseBlock:
    """Tests for pause block execution."""

    def test_pause_block(self, api_instance, fake_window):
        """Test pause block waits the specified duration."""
        blocks = [
            {
                "id": "b1",
                "type": "pause",
                "s": 0.15,
            }
        ]

        start = time.time()
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)
        duration = time.time() - start

        # Should have waited ~0.15s
        assert duration >= 0.12
        assert duration < 0.4

        events = fake_window.events
        # Should have block_start and block_end
        assert any(e["type"] == "block_start" for e in events)
        assert any(e["type"] == "block_end" for e in events)


class FakeTubing:
    """Stands in for the pump's tubing settings."""

    def __init__(self, mm=1.52):
        self.mm = {ch: mm for ch in "ABCD"}

    def get(self):
        return {"mm": dict(self.mm), "max_ml_per_min": {ch: 10 * mm for ch, mm in self.mm.items()}}

    def set(self, mm):
        self.mm = {ch: mm for ch in "ABCD"}
        return self.get()


@pytest.fixture
def fake_tubing(monkeypatch):
    tubing = FakeTubing()
    monkeypatch.setattr(hardware, "get_tubing", tubing.get)
    monkeypatch.setattr(hardware, "set_tubing", tubing.set)
    return tubing


def test_get_tubing(api_instance, fake_tubing):
    result = api_instance.get_tubing()
    assert result["ok"]
    assert result["mm"] == 1.52
    assert result["max_ml_per_min"]["A"] == pytest.approx(15.2)
    assert result["min_mm"] == 0.13 and result["max_mm"] == 3.17


def test_get_tubing_reports_mixed_channels(api_instance, fake_tubing):
    fake_tubing.mm["C"] = 0.13
    assert api_instance.get_tubing()["mm"] is None


def test_get_tubing_without_pump(api_instance, monkeypatch):
    def offline():
        raise OSError("no pump")
    monkeypatch.setattr(hardware, "get_tubing", offline)
    result = api_instance.get_tubing()
    assert not result["ok"]
    assert "no pump" in result["error"]
    assert result["mm"] is None and result["max_ml_per_min"] == {}


def test_set_tubing_sets_every_channel(api_instance, fake_tubing):
    result = api_instance.set_tubing(2.06)
    assert result["ok"]
    assert result["mm"] == 2.06
    assert set(fake_tubing.mm.values()) == {2.06}


def test_set_tubing_accepts_any_value_in_range(api_instance, fake_tubing):
    result = api_instance.set_tubing(2.1)
    assert result["ok"] and result["mm"] == 2.1


def test_set_tubing_rounds_to_hundredths(api_instance, fake_tubing):
    result = api_instance.set_tubing(1.504)
    assert result["ok"] and result["mm"] == 1.5


@pytest.mark.parametrize("bad", [0.1, 4, float("nan"), "abc", None])
def test_set_tubing_refuses_out_of_range(api_instance, fake_tubing, bad):
    result = api_instance.set_tubing(bad)
    assert not result["ok"]
    assert result["error"] == "Tubing must be 0.13-3.17 mm"
    assert result["mm"] == 1.52


def test_set_tubing_refused_while_running(api_instance, fake_tubing):
    api_instance.run_program([{"id": "p", "type": "pause", "s": 0.5}], 1)
    result = api_instance.set_tubing(2.06)
    assert not result["ok"]
    assert result["error"] == "Busy"
    assert result["mm"] == 1.52
    api_instance.stop()


def test_pump_connect_refused_while_busy(api_instance, monkeypatch):
    called = []
    monkeypatch.setattr(hardware, "connect", lambda: called.append(1) or hardware.info())
    api_instance.run_program([{"id": "p", "type": "pause", "s": 0.5}], 1)
    result = api_instance.pump_connect()
    api_instance.stop()
    assert not result["ok"] and result["error"] == "Busy"
    assert "mode" in result and "message" in result
    assert called == []


def test_pump_connect_ok(api_instance):
    result = api_instance.pump_connect()
    assert result["ok"] and result["mode"] == "mock"


def test_pump_connect_error(api_instance, monkeypatch):
    def boom():
        raise OSError("nope")
    monkeypatch.setattr(hardware, "connect", boom)
    result = api_instance.pump_connect()
    assert not result["ok"] and "nope" in result["error"]
    assert "mode" in result


def test_set_pump_logging(api_instance, monkeypatch):
    monkeypatch.setattr(hardware, "_logging", False)
    assert api_instance.set_pump_logging(True)["logging"] is True
    assert api_instance.set_pump_logging(False)["logging"] is False
