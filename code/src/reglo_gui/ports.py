"""Find the serial ports of the pump and the arm, on Linux, macOS and Windows.

The same device is /dev/ttyACM0 on Linux, /dev/cu.usbmodem1101 on macOS and
COM3 on Windows, so ports are found by their USB IDs instead of by name:

- The arm is an Arduino Nano (often a clone with a CH340 or FTDI USB chip)
  or an official Arduino. Matched by USB ID; reglo_gui.ArmController then
  checks each candidate by its BOOTED/PONG reply.
- The pump's USB ID is not known, so it is the one other USB serial port
  that answers the pump's version query.
  Its ID can be added to PUMP_VIDS once someone has looked it up
  (`python -m serial.tools.list_ports -v` with the pump plugged in).

All functions take the port list as an argument, so they can be tested
without hardware.
"""

import serial
from serial.tools import list_ports

# USB IDs a Nano (or clone) can show up with: (VID, PID), None = any PID.
ARM_USB_IDS = [
    (0x1A86, 0x7523),  # CH340 (most Nano clones)
    (0x1A86, 0x55D4),  # CH9102
    (0x0403, 0x6001),  # FTDI FT232R (older Nanos)
    (0x2341, None),    # Arduino
    (0x2A03, None),    # Arduino.org
]
# USB vendor IDs of the pump (none known yet).
PUMP_VIDS = {}
# Words in a port's description that mean it is the pump.
PUMP_WORDS = ("reglo", "ismatec")
PUMP_BAUD = 9600   # the pump's serial speed (manual section 14.1)


class PortError(Exception):
    pass


def usb_ports(ports=None):
    """The USB serial ports, sorted by name. Skips ports without a USB ID
    (built-in serial ports, Bluetooth on macOS), and on macOS the /dev/tty.*
    twin of each /dev/cu.* port (the tty.* one waits for a carrier)."""
    if ports is None:
        ports = list_ports.comports()
    usb = [p for p in ports if p.vid is not None
           and not p.device.startswith("/dev/tty.")]
    return sorted(usb, key=lambda p: p.device)


def describe(port):
    """One line about a port, e.g. 'COM3  1A86:7523  USB-SERIAL CH340'."""
    ids = f"{port.vid:04X}:{port.pid:04X}" if port.vid is not None else "-"
    return f"{port.device}  {ids}  {port.description or ''}".rstrip()


def describe_all(ports=None):
    """Every serial port, one per line, for error messages and the log."""
    if ports is None:
        ports = list_ports.comports()
    lines = [describe(p) for p in sorted(ports, key=lambda p: p.device)]
    return "\n".join(lines) or "(no serial ports found)"


def _is_pump(port):
    text = f"{port.description} {port.manufacturer} {port.product}".lower()
    return port.vid in PUMP_VIDS or any(w in text for w in PUMP_WORDS)


def _is_arm(port):
    return not _is_pump(port) and any(
        port.vid == vid and (pid is None or port.pid == pid)
        for vid, pid in ARM_USB_IDS)


def _one(found, what, ports):
    if len(found) == 1:
        return found[0].device
    if not found:
        raise PortError(f"No {what} found. Is it plugged in?\n\n"
                        f"Serial ports:\n{describe_all(ports)}")
    names = ", ".join(p.device for p in found)
    raise PortError(f"More than one port could be the {what}: {names}.\n"
                    f"Choose one with --{what}_port.\n\n"
                    f"Serial ports:\n{describe_all(ports)}")


def arm_candidates(ports=None):
    """Ports that could be the arm, by USB ID (not yet confirmed)."""
    return [p.device for p in usb_ports(ports) if _is_arm(p)]


def answers_like_pump(device):
    """True if `device` answers the pump's version query ('1#') within a
    second. Always closes the port again (Windows allows only one opener)."""
    try:
        with serial.Serial(device, PUMP_BAUD, timeout=1) as ser:
            ser.reset_input_buffer()
            ser.write(b"1#\r")
            return bool(ser.readline().strip())
    except (serial.SerialException, OSError):
        return False


def find_pump(ports=None, arm_port=None, probe=answers_like_pump):
    """Port of the pump: one that is known to be the pump, or else the one
    USB serial port that is not the arm (`arm_port`, or any arm board) and
    answers `probe`. (A USB-C dock can also show up as a serial port; and
    the pump library waits forever for a port that doesn't answer.)
    Raises PortError if there is none or several."""
    if ports is None:
        ports = list_ports.comports()
    usb = usb_ports(ports)
    known = [p for p in usb if _is_pump(p)]
    if known:
        return _one(known, "pump", ports)
    others = [p for p in usb if p.device != arm_port and not _is_arm(p)]
    answering = [p for p in others if probe(p.device)]
    if others and not answering:
        names = ", ".join(p.device for p in others)
        raise PortError(f"No pump found: no port answered ({names}). "
                        f"Is the pump on and plugged in?\n"
                        f"To use a port anyway, choose it with --pump_port.\n\n"
                        f"Serial ports:\n{describe_all(ports)}")
    return _one(answering, "pump", ports)
