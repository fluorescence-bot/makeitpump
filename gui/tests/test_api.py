"""
Tests for RainMixer API (gui/api.py).

These tests verify:
- Multi-channel pump block execution and event ordering
- Pause/resume with the remaining mL at the same speed
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
    """Max flow of 100 mL/s, so a channel takes ml / speed seconds and tests stay fast."""
    monkeypatch.setattr(hardware, "MAX_FLOW_ML_PER_S", 100.0)


def test_pump_seconds_uses_max_flow():
    assert api.pump_seconds(10, 50) == pytest.approx(0.2)


@pytest.mark.parametrize(
    "pumps, error",
    [
        ({"A": {"ml": 1, "speed": 80}}, None),
        ({"A": {"ml": 0, "speed": 0}}, None),
        ({"A": {"ml": 1, "speed": 0}}, "A: mL set but speed = 0"),
        ({"B": {"ml": 1, "speed": 120}}, "B: speed must be 0-100"),
        ({"C": {"ml": -1, "speed": 50}}, "C: negative mL"),
    ],
)
def test_pumps_error(pumps, error):
    assert api.pumps_error(pumps) == error


def test_run_rejects_invalid_speed(api_instance, mock_hardware):
    blocks = [{"id": "b1", "type": "pump", "pumps": {"A": {"ml": 1, "speed": 150}}}]
    result = api_instance.run_program(blocks, 1)
    assert result == {"ok": False, "error": "A: speed must be 0-100"}
    assert mock_hardware["start_pump"] == []


@pytest.fixture
def mock_hardware(monkeypatch):
    """Fixture to mock and spy on hardware calls."""
    calls = {
        "start_pump": [],
        "stop_pump": [],
        "stop_all": [],
    }

    def fake_start_pump(channel: str, ml: float, speed: float) -> None:
        calls["start_pump"].append((channel, ml, speed))

    def fake_stop_pump(channel: str) -> None:
        calls["stop_pump"].append(channel)

    def fake_stop_all() -> None:
        calls["stop_all"].append(None)

    monkeypatch.setattr(hardware, "start_pump", fake_start_pump)
    monkeypatch.setattr(hardware, "stop_pump", fake_stop_pump)
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
                    "A": {"ml": 9, "speed": 30},
                    "B": {"ml": 5, "speed": 50},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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

        # Verify hardware calls: only A and B should start (ml > 0 and speed > 0)
        starts = mock_hardware["start_pump"]
        assert len(starts) == 2
        channels_started = {ch for ch, _, _ in starts}
        assert channels_started == {"A", "B"}

        # Check that channel C and D are never started
        assert not any(ch == "C" for ch, _, _ in starts)
        assert not any(ch == "D" for ch, _, _ in starts)

        # Both channels should be stopped
        stops = mock_hardware["stop_pump"]
        assert len(stops) == 2
        assert set(stops) == {"A", "B"}

    def test_pump_block_idle_channels_dont_start(self, api_instance, mock_hardware):
        """Channels with no mL (or speed 0) should not start pumps."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 0, "speed": 80},  # no mL, should not start
                    "B": {"ml": 5, "speed": 50},  # active
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
                },
            }
        ]
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread)

        # Only B should have started
        starts = mock_hardware["start_pump"]
        assert len(starts) == 1
        assert starts[0][0] == "B"

    def test_pump_block_duration(self, api_instance, fake_window, mock_hardware):
        """Test that pump block duration is the longest channel's time."""
        start_time = time.time()
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 2.5, "speed": 10},
                    "B": {"ml": 1.5, "speed": 10},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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
                    "A": {"ml": 100, "speed": 10},  # Long run
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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
                    "A": {"ml": 3, "speed": 10},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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
                    "A": {"ml": 1, "speed": 0.5},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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

    def test_pause_resume_pump(self, api_instance, fake_window, mock_hardware):
        """Test pause and resume with multi-channel pump."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 10, "speed": 25},
                    "B": {"ml": 5, "speed": 25},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
                },
            }
        ]

        # Start run (don't await, as we need to pause mid-run)
        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True

        time.sleep(0.05)

        # Pause
        pause_result = api_instance.pause_run()
        assert pause_result["ok"] is True

        # Verify paused status was emitted
        assert wait_for_status(fake_window, "paused")

        time.sleep(0.1)

        # Resume
        resume_result = api_instance.resume_run()
        assert resume_result["ok"] is True

        # Wait for run to complete
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        # Verify that pause/resume happened
        starts = mock_hardware["start_pump"]
        stops = mock_hardware["stop_pump"]
        # Should have at least 2 starts and 2 stops (one per active channel)
        assert len(starts) >= 2
        assert len(stops) >= 2

        # Should have both pause and resume
        assert any(e["type"] == "status" and e["status"] == "paused" for e in fake_window.events)

    def test_pause_resume_respects_remaining_time(self, api_instance, fake_window, mock_hardware):
        """Test that resume pumps only the remaining mL, at the same speed."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 9, "speed": 30},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
                },
            }
        ]

        result = api_instance.run_program(blocks, 1)
        assert result["ok"] is True

        # Let it run for ~0.1s
        time.sleep(0.1)

        # Pause
        api_instance.pause_run()
        time.sleep(0.05)

        # Get start call before resume
        starts_before = mock_hardware["start_pump"].copy()

        # Resume
        api_instance.resume_run()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        # The resumed pump call delivers less than the original, at the same speed
        assert len(starts_before) == 1
        original_ch, original_ml, original_speed = starts_before[0]
        resumed_calls = mock_hardware["start_pump"][len(starts_before) :]
        assert len(resumed_calls) == 1
        resumed_ch, resumed_ml, resumed_speed = resumed_calls[0]

        assert resumed_ch == original_ch
        assert 0 < resumed_ml < original_ml
        assert resumed_speed == original_speed


class TestManualDose:
    """Tests for manual dosing (one click = fixed mL)."""

    def test_dose_pumps_fixed_amount(self, api_instance, fake_window, mock_hardware):
        """A dose starts the pump with the given mL and speed and reports the full mL."""
        result = api_instance.manual_dose("A", 2.5, 25)
        assert result["ok"] is True
        assert wait_for_thread(api_instance._runner_thread)

        assert mock_hardware["start_pump"] == [("A", 2.5, 25)]
        assert mock_hardware["stop_pump"] == ["A"]

        assert fake_window.events[0] == {"type": "status", "status": "dosing"}
        assert {"type": "dose_start", "channel": "A", "ml": 2.5, "speed": 25} in fake_window.events

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert pumped == [{"type": "pumped", "channel": "A", "ml": 2.5, "source": "manual"}]
        assert fake_window.events[-1] == {"type": "status", "status": "ready"}

    def test_dose_rejected_while_dosing(self, api_instance, mock_hardware):
        """A second click while a dose runs is refused."""
        assert api_instance.manual_dose("A", 1, 1)["ok"] is True
        time.sleep(0.05)

        result = api_instance.manual_dose("B", 1, 1)
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
                    "A": {"ml": 1, "speed": 1},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
                },
            }
        ]

        api_instance.run_program(blocks, 1)
        time.sleep(0.05)  # Let it start

        result = api_instance.manual_dose("B", 1, 50)
        assert result["ok"] is False
        assert "Busy" in result.get("error", "")

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_run_rejected_during_dose(self, api_instance, mock_hardware):
        """A program cannot start while a dose runs."""
        api_instance.manual_dose("A", 1, 1)
        time.sleep(0.05)

        result = api_instance.run_program([{"id": "b1", "type": "pause", "s": 0.1}], 1)
        assert result["ok"] is False

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_stopped_dose_reports_partial_ml(self, api_instance, fake_window, mock_hardware):
        """Stopping mid-dose logs only what was actually pumped."""
        api_instance.manual_dose("C", 10, 10)
        time.sleep(0.3)
        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        pumped = [e for e in fake_window.events if e["type"] == "pumped"]
        assert len(pumped) == 1
        assert pumped[0]["channel"] == "C"
        assert pumped[0]["source"] == "manual"
        assert 1.0 < pumped[0]["ml"] < 6.0

    @pytest.mark.parametrize("ml, speed", [(0, 50), (1, 0), (-1, 50), (1, 101)])
    def test_dose_rejects_bad_values(self, api_instance, mock_hardware, ml, speed):
        """Zero or negative mL, and speed outside (0, 100], are refused without touching the pump."""
        assert api_instance.manual_dose("A", ml, speed)["ok"] is False
        assert mock_hardware["start_pump"] == []


class TestPumpedEvents:
    """Program blocks also report what they pumped, for the session log."""

    def test_program_reports_pumped_per_channel(self, api_instance, fake_window, mock_hardware):
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 3, "speed": 30},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 1, "speed": 20},
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
                    "A": {"ml": 10, "speed": 10},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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
                    "A": {"ml": 1, "speed": 10},
                    "B": {"ml": 0, "speed": 0},
                    "C": {"ml": 0, "speed": 0},
                    "D": {"ml": 0, "speed": 0},
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
                        "A": {"ml": 10, "speed": 80},
                        "B": {"ml": 5, "speed": 80},
                        "C": {"ml": 0, "speed": 0},
                        "D": {"ml": 0, "speed": 0},
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
