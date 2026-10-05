import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dilution_controller import DilutionController

import asyncio

from ismatec import Pump
from dilution_controller import DilutionController

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger('ismatec').setLevel(logging.DEBUG)

# TODO: Change to COM if on if on Windows. /dev is unix specific.
ADDRESS = '/dev/ttyACM0'

def show(r):
    print(f"Step {r['step']}/{r['of']} done: {r['a_ml']} ml A, {r['b_ml']} ml B")

async def main():
    pump = Pump(ADDRESS);
    controller = DilutionController(pump)
    await controller.setup() 

    step_counter = 0
    
    is_finished = False

    # Example sequence. Volume in ml for channel 1 and 2 in each tuple.
    sequence = [(5,2,0,0),
                (0, 1, 0, 0)]

    input("Press Return to start the dilution (Ctrl+C to abort)...")
    
    while is_finished == False:
        result = await controller.run_step(sequence[step_counter])
        
        if step_counter < len(sequence):
            step_counter += 1
        
        print(f"Step {step_counter} completed.")
        
        input("Press Return for the next step...")

if __name__ == "__main__":
    asyncio.run(main())