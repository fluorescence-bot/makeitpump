"""Port detection with made-up port lists, as Windows, macOS and Linux show them."""

from types import SimpleNamespace

import pytest

import ports


def answers(device):
    """Probe stand-in: every port answers like a pump."""
    return True


def port(device, vid=None, pid=None, description="n/a", manufacturer=None,
         product=None):
    return SimpleNamespace(device=device, vid=vid, pid=pid,
                           description=description, manufacturer=manufacturer,
                           product=product)


NANO = dict(vid=0x1A86, pid=0x7523, description="USB-SERIAL CH340")
PUMP = dict(vid=0x1234, pid=0x5678, description="USB Serial Device")


def test_windows():
    found = [port("COM1", description="Communications Port"),
             port("COM3", **PUMP), port("COM4", **NANO)]
    assert ports.arm_candidates(found) == ["COM4"]
    assert ports.find_pump(found, probe=answers) == "COM3"


def test_linux():
    found = [port("/dev/ttyS0"), port("/dev/ttyACM0", **PUMP),
             port("/dev/ttyUSB0", **NANO)]
    assert ports.arm_candidates(found) == ["/dev/ttyUSB0"]
    assert ports.find_pump(found, probe=answers) == "/dev/ttyACM0"


def test_macos_skips_bluetooth_and_tty_twins():
    found = [port("/dev/cu.Bluetooth-Incoming-Port"),
             port("/dev/tty.usbmodem1101", **PUMP),
             port("/dev/cu.usbmodem1101", **PUMP),
             port("/dev/tty.usbserial-110", **NANO),
             port("/dev/cu.usbserial-110", **NANO)]
    assert ports.arm_candidates(found) == ["/dev/cu.usbserial-110"]
    assert ports.find_pump(found, probe=answers) == "/dev/cu.usbmodem1101"


def test_official_arduino_any_pid_is_an_arm_candidate():
    found = [port("COM3", **PUMP), port("COM5", vid=0x2341, pid=0x0043)]
    assert ports.arm_candidates(found) == ["COM5"]
    assert ports.find_pump(found, probe=answers) == "COM3"


def test_pump_found_by_name_even_among_other_devices():
    found = [port("COM3", vid=0x0001, pid=1, description="Some dongle"),
             port("COM6", vid=0x0002, pid=2, description="Reglo ICC"),
             port("COM4", **NANO)]
    assert ports.find_pump(found, probe=answers) == "COM6"


def test_given_arm_port_is_not_the_pump():
    other = dict(vid=0x0001, pid=1, description="USB Serial Device")
    found = [port("COM3", **PUMP), port("COM7", **other)]
    assert ports.find_pump(found, arm_port="COM7", probe=answers) == "COM3"


def test_nothing_plugged_in():
    found = [port("COM1", description="Communications Port")]
    assert ports.arm_candidates(found) == []
    with pytest.raises(ports.PortError, match="No pump found"):
        ports.find_pump(found, probe=answers)


DOCK = dict(vid=0x057E, pid=0, description="USB BillBoard")


def test_two_candidates_the_one_that_answers_wins():
    found = [port("/dev/cu.usbmodem1101", **PUMP),
             port("/dev/cu.usbmodemSN2345", **DOCK)]
    pump = ports.find_pump(found, probe=lambda d: d == "/dev/cu.usbmodem1101")
    assert pump == "/dev/cu.usbmodem1101"


def test_only_a_dock_is_not_taken_for_the_pump():
    found = [port("/dev/cu.usbmodemSN2345", **DOCK)]
    with pytest.raises(ports.PortError, match="no port answered"):
        ports.find_pump(found, probe=lambda d: False)


def test_two_pumps_answering_asks_to_choose():
    found = [port("COM3", **PUMP), port("COM8", **PUMP)]
    with pytest.raises(ports.PortError, match="--pump_port"):
        ports.find_pump(found, probe=answers)


def test_none_answers_lists_the_ports():
    found = [port("COM3", **PUMP), port("COM8", **DOCK)]
    with pytest.raises(ports.PortError, match="--pump_port") as err:
        ports.find_pump(found, probe=lambda d: False)
    assert "COM3  1234:5678  USB Serial Device" in str(err.value)
