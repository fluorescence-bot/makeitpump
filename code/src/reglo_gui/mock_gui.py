import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dilution_controller import DilutionController


from ismatec import command_line
from ismatec.mock import Pump
from ismatec.util import Mode, Rotation, Setpoint, Tubing


ADDRESS = '192.168.10.12:23'

def show(r):
    print(f"Step {r['step']}/{r['of']} done: {r['a_ml']} ml A, {r['b_ml']} ml B")

def main():
    pump = Pump(ADDRESS);
    controller = DilutionController(pump)

    # Example sequence. Volume in ml for channel 1 and 2 in each tuple.
    sequence = [(50, 1), (0, 1), (0, 1), (0, 1)]

    input("Press Return to start the dilution (Ctrl+C to abort)...")
    result = controller.start(sequence)

    try:
        while result is not None:
            show(result)
            input("Press Return for the next step...")
            result = controller.next_step()
        print("Dilution complete")
    except KeyboardInterrupt:
        controller.abort()
        print("\nAborted.")


if __name__ == "__main__":
    main()