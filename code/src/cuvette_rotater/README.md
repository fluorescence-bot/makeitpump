# Cuvette rotator firmware

CircuitPython firmware for the robot that rotates the cuvette between 0° and 60°.
Controlled by the GUI over USB serial (see issue #7).

## Hardware

- **Board:** Adafruit Trinket M0 (CircuitPython 10.3.1)
  - USB VID `0x239a`, PID `0x801f`, product string `Trinket M0` — used for port auto-detection
- **Motor:** 28BYJ-48 stepper via ULN2003 driver, half-stepping (4096 steps/rev)
  - D0–D3 → ULN2003 IN1–IN4
- **Home switch:** limit switch on D4 (internal pull-up, reads LOW when pressed) marks physical 0°

## Flashing

1. Plug the Trinket M0 in; it mounts as a USB drive.
2. Copy `code.py` to the root of the drive. CircuitPython restarts and runs it automatically.
3. The drive's `lib/` folder needs these from the
   [Adafruit CircuitPython bundle](https://circuitpython.org/libraries) (matching CircuitPython 10.x):
   - `adafruit_dotstar.mpy` (to switch off the on-board RGB LED so it doesn't disturb the optics)
   - `adafruit_pixelbuf.mpy` (dependency of `adafruit_dotstar`)

## Serial protocol

Newline-terminated commands over the USB serial port. Replies are `<...>` lines.

| Send | Board does | Replies |
|---|---|---|
| `HOME` | back off if on switch → sweep CCW until switch clicks → move 180 steps CW → set logical 0 | `<STATUS:HOMING>` … `<STATUS:READY>` |
| integer `0`–`60` | move to that absolute angle | `<STATUS:MOVING_TO_<deg>>`, `<STATUS:MOVE_COMPLETE>` |
| integer outside 0–60 | nothing | `<ERROR:OUT_OF_BOUNDS>` |

The board homes itself on boot. Opening the serial port does not reset it, so the host
should send `HOME` after connecting.

Known quirks (tracked in #7): decimals and unknown commands are silently ignored (no reply);
logical 0° is ~16° past the switch, not 90° as the code comment says.
