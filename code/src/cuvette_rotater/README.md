# Cuvette rotator firmware

Arduino firmware for the robot that rotates the cuvette between 0° and 60°.
Controlled by the GUIs over USB serial.

## Hardware

- **Board:** Arduino Nano clone (ATmega328P, 5 V logic, "old bootloader")
  - Shows up as a CH340 USB-serial port (`/dev/cu.usbserial-*` on macOS); the GUIs
    find it by USB ID and then by its `<STATUS:BOOTED>` / `<STATUS:PONG>` reply
- **Driver:** Pololu A4988, 1/16 microstepping (MS1, MS2, MS3 tied to 5 V), SLEEP and
  RESET bridged, 12 V on VMOT with a capacitor across it
  - D6 → STEP, D5 → DIR
- **Motor:** LDO-42STH20-1004AS1 pancake NEMA 17, 3200 microsteps/rev (0.1125°/step)
- **Home switch:** limit switch between D12 and GND (internal pull-up, reads LOW when
  pressed). Homing turns clockwise onto it; working moves turn counter-clockwise away
  from it.

## Flashing

Open `cuvette_rotater.ino` in the Arduino IDE, pick **Arduino Nano** with
**Tools → Processor → ATmega328P (Old Bootloader)**, and upload. From the command line:

```bash
arduino-cli compile --upload -p /dev/cu.usbserial-110 \
  --fqbn arduino:avr:nano:cpu=atmega328old code/src/cuvette_rotater
```

## Serial protocol

115200 baud, one command per line (case-insensitive). Replies are `<...>` lines.

| Send | Board does | Replies |
|---|---|---|
| (port opened) | the Nano restarts | `<STATUS:BOOTED>` |
| `PING` | nothing | `<STATUS:PONG>` |
| `HOME` | move off the switch if pressed → turn clockwise until the switch clicks (max 370°) → back off 139 steps (15.64°) → set 0° | `<STATUS:HOMING>` … `<STATUS:READY>`, or `<ERROR:SWITCH_NOT_FOUND>` / `<ERROR:SWITCH_STUCK>` |
| number `0`–`60` | move to that absolute angle | `<STATUS:MOVING_TO_<deg>>` (e.g. `MOVING_TO_30.0`), then `<STATUS:MOVE_COMPLETE>`, or `<ERROR:NOT_HOMED>` / `<ERROR:LIMIT_HIT>` |
| number outside 0–60 | nothing | `<ERROR:OUT_OF_BOUNDS>` |
| `STATUS` | nothing | `<STATUS:AT_<deg>>` or `<STATUS:NOT_HOMED>`, then `<STATUS:SWITCH_OPEN>` / `<STATUS:SWITCH_PRESSED>` |
| anything else | nothing | `<ERROR:UNKNOWN_COMMAND>` |

Errors for `HOME` and moves always come after their start line (`HOMING` /
`MOVING_TO_x`), so the GUI can tell them from stale replies.

The board does **not** home by itself: it restarts every time the port is opened, so
the GUI sends `HOME` after connecting.

0° is calibrated: it is `BACKOFF_STEPS` (139 microsteps, 15.64°) back from the switch.
To find it again (for example after moving the switch), run `calibrate.py` (needs
tkinter and pyserial, e.g. `../kawaii_gui/.venv/bin/python calibrate.py`), nudge the
cuvette to where 0° should be, press **Use this as 0°**, and put the number it shows in
`BACKOFF_STEPS`. It uses the calibration-only `JOG <deg>` command (relative move that
ignores the 0–60° range but stops at the switch; replies `<STATUS:AT_<deg>_STEP_<n>>`).
