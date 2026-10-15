import time
import board
import digitalio
import sys
import supervisor

# --- Turn Off Built-in RGB LED ---
# This prevents the Trinket's internal LED from polluting your optical setup
try:
    import adafruit_dotstar

    # Trinket M0 uses APA102 pins for its internal DotStar LED
    led = adafruit_dotstar.DotStar(board.APA102_SCK, board.APA102_MOSI, 1)
    led.brightness = 0.0
    led[0] = (0, 0, 0)
except ImportError:
    print(
        "<WARNING: adafruit_dotstar library not found in 'lib' folder. LED remains on.>"
    )

# --- Configuration ---
# 28BYJ-48 motor specs (using half-stepping for precision)
STEPS_PER_REV = 4096
# Calculate how many steps make up 90 degrees to find the laser baseline
CALIBRATION_OFFSET_STEPS = 180

# Delay between steps (seconds). Lower is faster.
STEP_DELAY = 0.002

# --- Hardware Setup (Trinket M0 Specific) ---
# Motor Pins (0, 1, 2, 3) mapped to ULN2003 (IN1, IN2, IN3, IN4)
motor_pins = [
    digitalio.DigitalInOut(board.D0),
    digitalio.DigitalInOut(board.D1),
    digitalio.DigitalInOut(board.D2),
    digitalio.DigitalInOut(board.D3),
]

for pin in motor_pins:
    pin.direction = digitalio.Direction.OUTPUT

# Limit Switch on Pin 4
limit_switch = digitalio.DigitalInOut(board.D4)
limit_switch.direction = digitalio.Direction.INPUT
limit_switch.pull = digitalio.Pull.UP  # Pulled HIGH naturally, goes LOW when clicked

# 8-Step Half-Stepping Sequence
step_sequence = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1],
]

current_step_index = 0
logical_position_steps = 0


def release_coils():
    # Turn off coils to prevent overheating when resting
    for pin in motor_pins:
        pin.value = 0


def move_steps(steps, direction, release=True):
    global current_step_index
    for _ in range(abs(steps)):
        if direction == "CW":
            current_step_index = (current_step_index + 1) % 8
        else:  # CCW
            current_step_index = (current_step_index - 1) % 8

        # Apply the power sequence to the pins
        for pin_idx in range(4):
            motor_pins[pin_idx].value = step_sequence[current_step_index][pin_idx]
        time.sleep(STEP_DELAY)

    if release:
        release_coils()


def execute_homing_sequence():
    global logical_position_steps
    print("<STATUS:HOMING>")

    # 1. If we are already resting on the limit switch, back off clockwise
    if not limit_switch.value:  # value is False (LOW) when clicked
        print("<STATUS:BACKING_OFF_SWITCH>")
        move_steps(200, "CW")  # Move about 17 degrees away
        time.sleep(0.5)

    # 2. Sweep counter-clockwise until the switch is clicked
    print("<STATUS:SEARCHING_FOR_ZERO>")
    while limit_switch.value:  # While NOT clicked
        # Keep coils energized between single steps so the motor actually turns
        move_steps(1, "CCW", release=False)
    release_coils()

    print("<STATUS:PHYSICAL_ZERO_FOUND>")
    time.sleep(0.5)

    # 3. Calibrate forward to 90 degrees
    print(f"<STATUS:CALIBRATING_{CALIBRATION_OFFSET_STEPS}_STEPS_CW>")
    move_steps(CALIBRATION_OFFSET_STEPS, "CW")

    logical_position_steps = 0
    print("<STATUS:READY>")


# --- Main Program Execution ---
execute_homing_sequence()

# Read from USB Serial continuously using supervisor (crash-safe for M0 chips)
while True:
    if supervisor.runtime.serial_bytes_available:
        command = sys.stdin.readline().strip()

        if command == "HOME":
            execute_homing_sequence()

        elif command.isdigit() or (command.startswith("-") and command[1:].isdigit()):
            target_degree = float(command)

            # Enforce your 0 to 60-degree limit to protect the tubes
            if target_degree < 0 or target_degree > 60:
                print("<ERROR:OUT_OF_BOUNDS>")
            else:
                target_steps = int((target_degree / 360) * STEPS_PER_REV)
                steps_to_move = target_steps - logical_position_steps

                direction = "CW" if steps_to_move > 0 else "CCW"

                print(f"<STATUS:MOVING_TO_{target_degree}>")
                move_steps(abs(steps_to_move), direction)

                logical_position_steps = target_steps
                print("<STATUS:MOVE_COMPLETE>")
