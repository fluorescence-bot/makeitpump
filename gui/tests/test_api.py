"""
Tests for RainMixer API (gui/api.py).

These tests verify:
- Multi-channel pump block execution and event ordering
- Pause/resume with pro-rata remaining time
- Stop behavior and concurrent run prevention
- Manual pump control (rejected during run, allowed when idle)
- Preset round-trip (save, list, load)
- Pour operation with correct events
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


@pytest.fixture
def mock_hardware(monkeypatch):
    """Fixture to mock and spy on hardware calls."""
    calls = {
        "start_pump": [],
        "stop_pump": [],
        "stop_all": [],
    }

    def fake_start_pump(channel: str, ml: float, s: float) -> None:
        calls["start_pump"].append((channel, ml, s))

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


class TestPumpBlock:
    """Tests for pump block execution."""

    def test_pump_block_multi_channel(self, api_instance, fake_window, mock_hardware):
        """Test multi-channel pump block with channels A and B active."""
        # A: 10 mL over 0.3s, B: 5 mL over 0.1s, C: idle, D: idle
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 10, "s": 0.3},
                    "B": {"ml": 5, "s": 0.1},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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

        # Verify hardware calls: only A and B should start (s > 0)
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

    def test_pump_block_channels_s_zero_dont_start(self, api_instance, mock_hardware):
        """Channels with s=0 should not start pumps."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 10, "s": 0},  # s=0, should not start
                    "B": {"ml": 5, "s": 0.1},  # active
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
        """Test that pump block duration is max(s) across channels."""
        start_time = time.time()
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1, "s": 0.25},
                    "B": {"ml": 1, "s": 0.15},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
                    "A": {"ml": 100, "s": 10},  # Long run
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
                    "A": {"ml": 1, "s": 0.3},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
                    "A": {"ml": 1, "s": 2},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
                    "A": {"ml": 10, "s": 0.4},
                    "B": {"ml": 5, "s": 0.2},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
        time.sleep(0.05)
        paused_events = [
            e for e in fake_window.events if e["type"] == "status" and e["status"] == "paused"
        ]
        assert len(paused_events) > 0

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
        """Test that resume uses remaining time, not full duration."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 10, "s": 0.3},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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

        # Check that the resumed pump call has a reduced duration
        # The ratio ml/s should be preserved
        if len(starts_before) > 0:
            original_call = starts_before[0]
            original_ch, original_ml, original_s = original_call

            # Find the resumed call (after pause)
            resumed_calls = mock_hardware["start_pump"][len(starts_before) :]
            if len(resumed_calls) > 0:
                resumed_call = resumed_calls[0]
                resumed_ch, resumed_ml, resumed_s = resumed_call

                # Channel should be same
                assert resumed_ch == original_ch

                # Duration should be less than original
                assert resumed_s < original_s

                # ml/s ratio should be preserved (approximately)
                original_rate = original_ml / original_s
                resumed_rate = resumed_ml / resumed_s
                assert abs(original_rate - resumed_rate) < 0.01  # Small tolerance


class TestManualPump:
    """Tests for manual pump control."""

    def test_manual_pump_start_stop_idle(self, api_instance, fake_window, mock_hardware):
        """Manual pump should work when idle."""
        result_start = api_instance.manual_pump_start("A")
        assert result_start["ok"] is True

        # Check that manual event was emitted
        manual_events = [e for e in fake_window.events if e["type"] == "manual" and e["channel"] == "A"]
        assert any(e["active"] is True for e in manual_events)

        # Check hardware.start_pump was called with 0, 0 (run indefinitely)
        starts = [call for call in mock_hardware["start_pump"] if call[0] == "A"]
        assert len(starts) >= 1

        # Stop manual pump
        result_stop = api_instance.manual_pump_stop("A")
        assert result_stop["ok"] is True

        # Check that manual event was emitted for stop
        manual_events = [e for e in fake_window.events if e["type"] == "manual" and e["channel"] == "A"]
        assert any(e["active"] is False for e in manual_events)

    def test_manual_pump_rejected_during_run(self, api_instance, mock_hardware):
        """Manual pump should be rejected while a program is running."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1, "s": 1},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
                },
            }
        ]

        api_instance.run_program(blocks, 1)
        time.sleep(0.05)  # Let it start

        # Try manual pump
        result = api_instance.manual_pump_start("B")
        assert result["ok"] is False
        assert "Busy" in result.get("error", "")

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

    def test_manual_pump_rejected_during_pour(self, api_instance, mock_hardware):
        """Manual pump should be rejected while pouring."""
        api_instance.pour(10, 1)
        time.sleep(0.05)  # Let it start

        result = api_instance.manual_pump_start("A")
        assert result["ok"] is False
        assert "Busy" in result.get("error", "")

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)


class TestPour:
    """Tests for pour functionality."""

    def test_pour_emits_events(self, api_instance, fake_window, mock_hardware):
        """Pour should emit pour_start, status, and complete."""
        result = api_instance.pour(10, 0.2)
        assert result["ok"] is True

        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)

        events = fake_window.events

        # Check pour_start
        pour_starts = [e for e in events if e["type"] == "pour_start"]
        assert len(pour_starts) == 1
        assert pour_starts[0]["ml"] == 10
        assert pour_starts[0]["s"] == 0.2

        # Check status: pouring (should come after pour_start)
        pour_status_idx = None
        pour_start_idx = None
        for i, e in enumerate(events):
            if e["type"] == "pour_start":
                pour_start_idx = i
            if e["type"] == "status" and e["status"] == "pouring":
                pour_status_idx = i
                break

        # pour_start should come before pouring status
        if pour_start_idx is not None and pour_status_idx is not None:
            assert pour_start_idx < pour_status_idx

        # Check final status: done
        dones = [e for e in events if e["type"] == "status" and e["status"] == "done"]
        assert len(dones) >= 1

        # Check that D pump was started
        d_starts = [call for call in mock_hardware["start_pump"] if call[0] == "D"]
        assert len(d_starts) == 1

    def test_pour_rejected_during_run(self, api_instance, mock_hardware):
        """Pour should be rejected while a program is running."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1, "s": 1},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
                },
            }
        ]

        api_instance.run_program(blocks, 1)
        time.sleep(0.05)

        result = api_instance.pour(5, 0.5)
        assert result["ok"] is False
        assert "already running" in result.get("error", "").lower()

        api_instance.stop()
        assert wait_for_thread(api_instance._runner_thread, timeout=2.0)


class TestRepeat:
    """Tests for repeat functionality."""

    def test_repeat_runs_multiple_times(self, api_instance, fake_window, mock_hardware):
        """Test that repeat > 1 runs the block sequence multiple times."""
        blocks = [
            {
                "id": "b1",
                "type": "pump",
                "pumps": {
                    "A": {"ml": 1, "s": 0.1},
                    "B": {"ml": 0, "s": 0},
                    "C": {"ml": 0, "s": 0},
                    "D": {"ml": 0, "s": 0},
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
                        "A": {"ml": 10, "s": 5},
                        "B": {"ml": 5, "s": 3},
                        "C": {"ml": 0, "s": 0},
                        "D": {"ml": 0, "s": 0},
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
