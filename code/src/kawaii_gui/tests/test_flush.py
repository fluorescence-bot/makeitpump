"""
Flushing and cleaning: the volumes (flush.py), the saved tube lengths, and the
Api's flush/clean calls, which pump but are never counted (no `pumped` events).
"""

import time

import pytest

import api
import flush
import hardware
from test_api import FakeWindow, mock_hardware, wait_for_status  # noqa: F401 (fixture)


# The validation cases worked out by hand (2.79 mm tube, 3.0 mL excess)
@pytest.mark.parametrize("length_mm, internal_ml, total_ml", [
    (100, 0.61, 3.61),
    (304, 1.86, 4.86),
    (500, 3.06, 6.06),
    (1000, 6.11, 9.11),
])
def test_flush_volumes(length_mm, internal_ml, total_ml):
    assert round(flush.tube_volume_ml(length_mm), 2) == internal_ml
    assert round(flush.flush_ml(length_mm), 2) == total_ml


def test_volume_per_mm_matches_the_constant():
    assert flush.tube_volume_ml(1) == pytest.approx(0.0061136, rel=1e-4)


@pytest.mark.parametrize("value, ok", [(304, True), ("304", True), (0.5, True), (0, False), (-10, False),
                                       (20000, False), ("abc", False), (None, False)])
def test_tube_length_error(value, ok):
    assert (flush.tube_length_error(value) is None) == ok


def test_clean_plan_rinses_then_drains_each_round():
    plan = flush.clean_plan([100, 200, 300, 400], ml_per_s=1.0)
    f = [flush.flush_ml(length) for length in (100, 200, 300, 400)]
    rinse, drain = (f[0], f[1], f[2], 0.0), (0.0, 0.0, 0.0, sum(f))
    assert plan["steps"] == [rinse, drain] * flush.CLEAN_ROUNDS
    assert plan["water_ml"] == [pytest.approx(flush.CLEAN_ROUNDS * x) for x in f[:3]]
    assert plan["seconds"] == pytest.approx(flush.CLEAN_ROUNDS * (max(f[:3]) + sum(f)))


def test_reglo_gui_has_the_same_flush_py():
    here = flush.__file__
    other = here.replace("kawaii_gui", "reglo_gui")
    with open(here, encoding="utf-8") as a, open(other, encoding="utf-8") as b:
        assert a.read() == b.read(), "kawaii_gui/flush.py must be a verbatim copy of reglo_gui/flush.py"


# -- Api ----------------------------------------------------------------------

@pytest.fixture
def window():
    return FakeWindow()


@pytest.fixture
def a(window, tmp_path, monkeypatch, mock_hardware):  # noqa: F811
    monkeypatch.setattr(api.Api, "_settings_path", lambda self: tmp_path / "settings.yaml")
    monkeypatch.setattr(hardware, "FLOW_ML_PER_S", 10.0)
    instance = api.Api()
    instance.set_window(window)
    return instance


def wait_idle(a, timeout=3.0):
    end = time.monotonic() + timeout
    while a._busy() and time.monotonic() < end:
        time.sleep(0.01)
    assert not a._busy()


class TestTubeLengths:
    def test_unset_by_default(self, a):
        assert a.get_tube_lengths() == {"ok": True, "mm": {"A": None, "B": None, "C": None, "D": None}}

    def test_set_and_saved(self, a, tmp_path):
        result = a.set_tube_lengths({"A": 304, "D": "500"})
        assert result == {"ok": True, "mm": {"A": 304.0, "B": None, "C": None, "D": 500.0}}
        assert (tmp_path / "settings.yaml").exists()
        assert api.Api.get_tube_lengths(a)["mm"]["A"] == 304.0      # read back from the file

    def test_null_clears_one(self, a):
        a.set_tube_lengths({"A": 304, "B": 200})
        assert a.set_tube_lengths({"A": None})["mm"] == {"A": None, "B": 200.0, "C": None, "D": None}

    @pytest.mark.parametrize("mm", [{"A": -1}, {"A": "x"}, {"A": True}, {"E": 100}])
    def test_refuses_bad_values(self, a, mm):
        result = a.set_tube_lengths(mm)
        assert result["ok"] is False and result["error"]
        assert result["mm"] == {"A": None, "B": None, "C": None, "D": None}


class TestFlush:
    def test_flush_pumps_tube_volume_plus_excess(self, a, window, mock_hardware):  # noqa: F811
        a.set_tube_lengths({"A": 100, "C": 304})
        assert a.flush(["A", "C"]) == {"ok": True}
        assert wait_for_status(window, "ready", timeout=3)
        expected = {"A": flush.flush_ml(100), "C": flush.flush_ml(304)}
        assert mock_hardware["run_step"] == [expected]
        assert [e for e in window.events if e["type"] != "status"] == [{"type": "flush_start", "volumes": expected}]
        assert [e["status"] for e in window.events if e["type"] == "status"] == ["flushing", "ready"]

    def test_flush_is_not_counted(self, a, window):
        a.set_tube_lengths({"D": 100})
        a.flush(["D"])
        assert wait_for_status(window, "ready", timeout=3)
        assert not any(e["type"] == "pumped" for e in window.events)

    def test_refused_without_length(self, a, mock_hardware):  # noqa: F811
        a.set_tube_lengths({"A": 100})
        assert a.flush(["A", "B"]) == {"ok": False, "error": "Set the tube length of B first"}
        assert mock_hardware["run_step"] == []

    @pytest.mark.parametrize("channels", [[], ["E"]])
    def test_refuses_bad_channels(self, a, channels):
        assert a.flush(channels)["ok"] is False

    def test_refused_while_busy(self, a):
        a.set_tube_lengths({"A": 1000, "B": 1000})
        assert a.flush(["A"])["ok"] is True
        assert a.flush(["B"]) == {"ok": False, "error": "Busy"}
        assert a.manual_dose("B", 1) == {"ok": False, "error": "Busy"}
        a.stop()
        wait_idle(a)

    def test_stop(self, a, window, mock_hardware):  # noqa: F811
        a.set_tube_lengths({"A": 1000})
        a.flush(["A"])
        assert wait_for_status(window, "flushing")
        a.stop()
        wait_idle(a)
        assert mock_hardware["stop_all"]
        statuses = [e["status"] for e in window.events if e["type"] == "status"]
        assert statuses == ["flushing", "stopped"]


class TestClean:
    @pytest.fixture(autouse=True)
    def small_excess(self, monkeypatch):
        monkeypatch.setattr(flush, "FLUSH_EXCESS_ML", 0.1)   # keep the fake pump quick

    def test_refused_until_every_length_is_set(self, a):
        a.set_tube_lengths({"A": 10, "B": 10, "C": 10})
        assert a.clean() == {"ok": False, "error": "Set the tube length of D first"}

    def test_clean_rinses_and_drains_each_round(self, a, window, mock_hardware):  # noqa: F811
        a.set_tube_lengths({"A": 10, "B": 20, "C": 30, "D": 40})
        assert a.clean() == {"ok": True}
        assert wait_for_status(window, "ready", timeout=5)
        plan = flush.clean_plan([10, 20, 30, 40], 10.0)
        assert mock_hardware["run_step"] == [
            {ch: ml for ch, ml in zip("ABCD", step) if ml > 0} for step in plan["steps"]]
        messages = [e.get("message") for e in window.events if e["type"] == "status" and e["status"] == "cleaning"]
        assert messages[1] == "Round 1/3: rinsing A-C into the bowl"
        assert messages[-1] == "Round 3/3: draining the bowl through D"
        assert not any(e["type"] == "pumped" for e in window.events)

    def test_stop_ends_the_clean(self, a, window, mock_hardware, monkeypatch):  # noqa: F811
        monkeypatch.setattr(flush, "FLUSH_EXCESS_ML", 5.0)
        a.set_tube_lengths({"A": 10, "B": 10, "C": 10, "D": 10})
        a.clean()
        assert wait_for_status(window, "cleaning")
        a.stop()
        wait_idle(a)
        assert len(mock_hardware["run_step"]) == 1
        assert not any(e["type"] == "status" and e["status"] == "ready" for e in window.events)
