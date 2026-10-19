"""ArmController against a fake serial port that answers like the Nano sketch
(../cuvette_rotater/cuvette_rotater.ino)."""

import pytest

import reglo_gui
from reglo_gui import ArmController, ArmError


class FakeSerial:
    """Gives back `replies[cmd]` as lines after each written command."""

    def __init__(self, replies, boot=()):
        self.replies = replies
        self.lines = list(boot)
        self.written = []
        self.is_open = True

    def readline(self):
        return (self.lines.pop(0) + "\r\n").encode() if self.lines else b""

    def write(self, data):
        cmd = data.decode().strip()
        self.written.append(cmd)
        self.lines += self.replies.get(cmd, [])

    def reset_input_buffer(self):
        self.lines.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False


NANO = {
    "PING": ["<STATUS:PONG>"],
    "HOME": ["<STATUS:HOMING>", "<STATUS:READY>"],
    "30": ["<STATUS:MOVING_TO_30.0>", "<STATUS:MOVE_COMPLETE>"],
}


@pytest.fixture(autouse=True)
def fast_boot(monkeypatch):
    monkeypatch.setattr(reglo_gui, "ARM_BOOT_TIMEOUT", 0.05)


@pytest.fixture
def arm(monkeypatch):
    fake = FakeSerial(dict(NANO), boot=["<STATUS:BOOTED>"])
    monkeypatch.setattr(reglo_gui.serial, "Serial", lambda *a, **kw: fake)
    arm = ArmController("COM4", 115200, timeout=0.05)
    arm.connect()
    return arm, fake


def test_home_and_rotate(arm):
    arm, fake = arm
    arm.home()
    arm.rotate(30)
    assert fake.written == ["HOME", "30"]


def test_error_reply_fails(arm):
    arm, fake = arm
    fake.replies["30"] = ["<ERROR:NOT_HOMED>"]
    with pytest.raises(ArmError, match="NOT_HOMED"):
        arm.rotate(30)


def test_no_reply_times_out(arm):
    arm, fake = arm
    fake.replies["HOME"] = ["<STATUS:HOMING>"]
    with pytest.raises(ArmError, match="No reply"):
        arm.home()


def test_board_that_did_not_restart_is_pinged(monkeypatch):
    fake = FakeSerial(dict(NANO))
    monkeypatch.setattr(reglo_gui.serial, "Serial", lambda *a, **kw: fake)
    ArmController("COM4", 115200, timeout=0.05).connect()
    assert fake.written == ["PING"]


def test_auto_detect_skips_a_port_that_doesnt_answer(monkeypatch):
    boards = {"COM3": FakeSerial({}),
              "COM4": FakeSerial(dict(NANO), boot=["<STATUS:BOOTED>"])}
    monkeypatch.setattr(reglo_gui.ports, "arm_candidates", lambda: ["COM3", "COM4"])
    monkeypatch.setattr(reglo_gui.serial, "Serial",
                        lambda port, *a, **kw: boards[port])
    arm = ArmController(None, 115200, timeout=0.05)
    arm.connect()
    arm.home()
    assert boards["COM4"].written == ["HOME"]
    assert not boards["COM3"].is_open


def test_no_arm_plugged_in_lists_the_ports(monkeypatch):
    monkeypatch.setattr(reglo_gui.ports, "arm_candidates", lambda: [])
    monkeypatch.setattr(reglo_gui.ports, "describe_all",
                        lambda: "COM1  -  Communications Port")
    with pytest.raises(ArmError, match="COM1"):
        ArmController(None, 115200, timeout=0.05).connect()


def test_flags_default_to_real_hardware():
    args = reglo_gui.parse_args([])
    assert not (args.enable_logging or args.mock_pump or args.mock_arm)
    assert args.pump_port is None and args.arm_port is None
