# Reglo GUI

A Tkinter desktop app that runs a mixing sequence on an Ismatec Reglo ICC
peristaltic pump and sets the angle of the cuvette rotator arm.

Channels 1–3 pump liquids into the mixing bowl, and channel 4 pumps the bowl
out to the measuring instrument. The program is a list of steps that you run
one at a time or all in one go.

## Setup

You need Python 3 with Tkinter. You also need SSH access to GitHub, because
the pump library (`ismatec-control`) installs from
[drivs/ismatec-pump](https://github.com/drivs/ismatec-pump) over SSH.

```bash
cd reglo_gui
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python reglo_gui.py
```

## Configuration

The settings are constants at the top of `reglo_gui.py`:

| Setting | Meaning |
|---|---|
| `DEBUGGING` | `True` uses the mock pump and a mock arm, so no hardware is needed. Set it to `False` to drive the real devices. |
| `PUMP_ADDRESS` | Serial port of the pump, e.g. `/dev/ttyACM0` or `COM3`. |
| `SERIAL_PORT`, `SERIAL_BAUD` | Serial port and baud rate of the rotator arm. This must be a different port from the pump. |
| `PRESETS` | The built-in presets. |

The rotator arm's serial protocol is described in
[`firmware/cuvette_rotator/`](../firmware/cuvette_rotator/).

## Using it

1. **Choose a preset** in the drop-down and press **OK**. A preset holds the
   steps plus these settings:
   - **Rotation**: arm angle, a whole number of degrees from 0 to 60.
   - **Ch 4 output volume**: how much a Pump out step pumps out, in ml.
   - **Mixing pause**: seconds to wait after each Pump in step.
   - **Measurement pause**: the default length of a new Pause step, in
     seconds.

   Each setting is locked (🔒) to prevent accidental edits. Click the lock to
   edit it, and click ↶ to go back to the saved value. Click ✎ next to the
   preset's name to rename the preset.
2. **Edit the steps.** There are three step types:
   - **Pump in**: pumps the volumes (ml) in columns 1–3 on channels 1–3,
     then waits for the mixing pause.
   - **Pump out**: pumps the preset's output volume on channel 4.
   - **Wait**: waits the given number of seconds. The rest of the program
     can still be used while it waits.

   Use **Add row: Pause / Output / Input** under the table to add a step.
   Click 🗑 to delete a step that hasn't run yet; it asks you to confirm
   first.
3. **Press Initialize.** This sets up the pump, homes the arm and turns it to
   the preset's rotation.
4. **Run the steps:**
   - **Run cur. step** runs the highlighted step.
   - **Run all** goes through the remaining steps without any further input.
   - **Stop after step** ends a Run all once the current step has finished.
     It never stops in the middle of pumping.

   Finished steps are ticked and greyed out. During a wait, or the mixing
   pause, you can still edit the steps that come next.

**Save changes** stores the preset, but only in memory: changes are lost when
the program closes until presets are stored in a database (see the
milestones).

## Files

- `reglo_gui.py`: the GUI, the arm controller and the presets.
- `dilution_controller.py`: turns a step into pump commands for the
  `ismatec` library.
- `mock_gui.py`: an old command-line test script. It is out of date and no
  longer runs, because it uses `DilutionController` methods that were removed.

## Milestones

- [ ] **Pause mid-step**, in both single-step and Run all mode.
  The pump does not support this well: it has no pause command, and stopping
  a channel loses its count of how much it has pumped. Resuming would have to
  estimate the delivered volume (flow rate × running time) and restart each
  channel with the remaining volume.
- [ ] **Store presets in SQLite.**
- [ ] **Sessions**: a session runs from Initialize until the session ends.
- [ ] **Log each step of a session.**
  - [ ] Store the logs in SQLite.
- [ ] **Log explorer**: a button for browsing past logs.
  - [ ] Export logs to XLS or CSV.
- [ ] **Take tube size into account.**
- [ ] **Show progress while pumping**, using the pump's `^U` event messages.
- [ ] **Program options**, saved in SQLite: for example time as min:sec or
  seconds, and volumes in ml or µl.
