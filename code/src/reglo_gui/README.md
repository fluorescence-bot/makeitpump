# Reglo GUI

A Tkinter desktop app that runs a mixing sequence on an Ismatec Reglo
ICC peristaltic pump and sets the angle of the cuvette rotator arm.

Channels 1–3 pump liquids into the mixing bowl, and channel 4 pumps
the bowl out to the measuring instrument. The program is a list of
steps that you run one at a time or all in one go.

## Setup

You need Python 3 with Tkinter.

```bash
cd reglo_gui
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python reglo_gui.py
```

To try it without the pump, start it with `--mock_pump` (and `--mock_arm`
without the rotator).

### App icon and launcher on Linux

GNOME shows an app's icon in the dock and Alt-Tab only if the app has
a desktop entry. (GNOME never shows icons in window title bars.) To
add one, which also adds "Reglo ICC Pump Control" to the app launcher:

```bash
.venv/bin/python install_desktop_entry.py           # install or update
.venv/bin/python install_desktop_entry.py --remove  # remove it again
```

This writes `~/.local/share/applications/reglogui.desktop`, which starts
the app with the Python you ran it with. Run it again if you move the
folder or recreate the venv.

## Configuration

Connection and database defaults are constants near the top of
`reglo_gui.py`; runtime logging and mock-device options are command-line
flags:

| Setting | Meaning |
|---|---|
| `--enable_logging` | Show debug logging in the terminal. Off by default. |
| `--mock_pump` | Use a mock pump. Off by default. |
| `--mock_arm` | Use a mock arm. Off by default. |
| `--pump_port`, `PUMP_ADDRESS` | Serial port of the pump, e.g. `/dev/ttyACM0`, `/dev/cu.usbmodem…` (macOS) or `COM3`. |
| `--arm_port`, `SERIAL_PORT` | Serial port of the rotator arm (Arduino Nano). By default the Nano is found automatically: the first Arduino-like USB serial port that answers. |
| `SERIAL_BAUD` | Baud rate of the rotator arm, 115200 (must match the sketch). |
| `DB_PATH` | The database for presets and session logs, `data.db` next to `reglo_gui.py` by default. |
| `DEFAULT_PRESETS` | The example presets put into a new, empty database. |

The rotator arm's serial protocol is described in
[`cuvette_rotater/`](../cuvette_rotater/).

## Using it

1. **Choose a preset** in the drop-down and press **Load**. A preset holds the
   steps plus these settings:
   - **Rotation**: arm angle, a whole number of degrees from 0 to 60.
   - **Ch 4 output volume**: how much a Pump out step pumps out, in ml.
   - **Mixing pause**: seconds to wait after each Pump in step.
   - **Measurement pause**: the default length of a new Pause step, in
     seconds.
   - **Tubing inner diameter**: select one of the pump's supported sizes
     in millimetres. It is stored with the preset and applied to all
     pump channels when saved and when a new session starts.
   - **Direction**: clockwise (the default) or counterclockwise. Like the
     tubing size, it is set on all four pump channels at once, when saved
     and when a new session starts.

   Each setting is locked (🔒) to prevent accidental edits. Click the
   lock to edit it, and click ↶ to go back to the saved value. Click ✎
   next to the preset's name to rename the preset.

   **New preset…** creates a preset, starting from either a copy of the
   current one (its settings and steps as shown, including unsaved
   edits) or an empty one. Give it a name and press **Create**: the new
   preset is saved and opened. The original preset keeps its saved
   version.
2. **Edit the steps.** There are three step types:
   - **Pump in**: pumps the volumes (ml) in columns 1–3 on channels 1–3,
     then waits for the mixing pause.
   - **Pump out**: pumps the preset's output volume on channel 4.
   - **Wait**: waits the given number of seconds. The rest of the program
     can still be used while it waits.

   Use **Add row: Pause / Output / Input** under the table to add a step.
   Click 🗑 to delete a step that hasn't run yet; it asks you to
   confirm first.
3. **Press New session.** This sets up the pump, homes the arm and turns
   it to the preset's rotation, then starts a new session from step 1
   (see below). If setting up the pump or arm fails, no session is
   started.
4. **Run the steps:**
   - **Run cur. step** runs the highlighted step.
   - **Run all** goes through the remaining steps without any further
     input.
   - **Stop after step** ends a Run all once the current step has
     finished.  It never stops in the middle of pumping.

   Finished steps are ticked and greyed out. During a wait, or the
   mixing pause, you can still edit the steps that come next.

## Sessions

A session runs from **New session** until you start another one, load
another preset, or exit.  It logs every step that finishes: when the
step started, its type, how many ml were pumped on each of channels
1–4, and for a Wait step how many seconds it waited. A Wait step logs
0 ml on every channel. A step that fails with a pump error is not
logged.

The session is saved to the database after every step, so nothing is
lost if the program stops. If an automatic save fails (for example, a
full disk), the error is shown and **Run all** stops, so no more steps run
without being logged; the next step's save includes the unsaved steps.

**New session**, **Load**, **New preset…** (which opens the new preset) and **Exit**
(or closing the window) end the current session, so they warn first
and ask to continue (**OK**) or go back (**Cancel**). If an automatic save
failed, they instead ask whether to save the unsaved steps first: **Yes**
saves them, **No** discards them, and **Cancel** goes back. After loading a
preset, press **New session** to start running it.

Each saved session has a header (when it started, and the preset's
name and settings as they were used) and a footer (when it was last
saved and how many steps it had then).

### Log explorer

**Log explorer…** (next to Exit) lists the saved sessions, newest
first. Each is named `<start date and time>-<preset name>`, for example
`2026-10-06_14-03-12-preset1`. Select a session to:

- **Open** it (or double-click it): shows the log as text, with the
  header, one line per step and the footer, in the system's default
  text editor.
- **Export to CSV…**: opens a save dialog with `<session name>.csv` as the
  suggested file name. The first export starts in the folder the
  program was started from, later ones in the folder you last exported
  to. After saving, it shows where the file went. The columns are
  `step#`, `type`, `ch1` to `ch4` (ml) and `wait` (seconds). Fields that don't
  apply to a step's type are `n/a`: a Pump in step uses ch1–ch3, a Pump
  out step ch4, and a Wait step only `wait`.

Wait steps saved before the wait time was logged show `n/a` as their
wait.

## Database

Presets and session logs are stored in an SQLite database, `data.db`.
**Save changes** and renaming write presets to it immediately, so they are
kept when the program closes. The first time the program runs, it
creates the database and fills it with the example presets. To start
over, close the program and delete `data.db`; this also deletes all
saved sessions. The file is not tracked by git. A database from before
sessions existed (`presets.db`) is renamed to `data.db` automatically,
keeping its presets.

The database has these tables:

| Table | Holds |
|---|---|
| `presets` | One row per preset with its settings. |
| `steps` | The steps of each preset, in order. |
| `sessions` | One row per saved session: the header and footer. |
| `session_steps` | The logged steps of each session: time, type, ml on ch 1–4, and seconds waited. |

Preset values are stored as the text that was typed, so they load back
exactly as entered.

## Files

- `reglo_gui.py`: the GUI, the arm controller and the default presets.
- `data_store.py`: reads and writes presets and sessions in the SQLite
  database.
- `session_log.py`: turns a saved session into a CSV file or a text log.
- `pump-arm-icon.png`: the window icon (`pump-arm-icon.svg`: the icon as
  SVG).
- `install_desktop_entry.py`: adds the Linux desktop entry for the app
  icon.
- `dilution_controller.py`: turns a step into pump commands for the
  `ismatec` library.
- `mock_gui.py`: an old command-line test script. It is out of date and
  no longer runs, because it uses `DilutionController` methods that were
  removed.

## Milestones

### Done

- [x] **Store presets in SQLite.**
- [x] **Sessions**: a session runs from New session until the session
      ends.
- [x] **Log each step of a session.**
  - [x] Store the logs in SQLite.
- [x] **Log explorer**: a button for browsing past logs.
  - [x] Export logs to CSV.
- [x] **Tubing size and direction** per preset, applied to all channels.

### To do

* [ ] **Log preset changes** if changes are made to preset (e.g. mixing
      time) during a trial run, these changes should also noted in the
      session logs. It could be by insering a INFO step with only it's
      'Comment' column populated. This way we will note break the csv
      format, and we only need to add a 'Comment'/'Notes' column.
* [ ] **Units** write ml (milliliter) next to each channel header. E.g. 1
      (ml), 2 (ml), etc.
* [ ] **Repeat** make it possible to repeat a selected X, or step X og
      X+1. Instead of a trashbin, three dots will be visible for more
      options. When presse, these options should be present:
  * Repeat current step for Y times...
  * Repeat current and next step Y times...
  * Repeat current and next X steps Y times... Generic option of the above.
  * Delete current step the
    
  'X times...' will open a dialog asking how
  many times to repeat current (or current plus next) step.

- [ ] **To the above three button**, another option should be 'insert new step
  after current step'. This way you can add any missing steps in the
  middle. Already run steps cannot be deleted, but the repeat and
  insert X times are still available.

### Shelved/archived

Shelved/archived if not deemed important for prototype.

- [ ] **Restore a session** if application crashed.
- [ ] **Show progress while pumping**, using the pump's `^U` event messages.
- [ ] **Program options**, saved in SQLite: for example time as min:sec or
      seconds, and volumes in ml or µl.
- [ ] **Export logs to XLS** from the log explorer. Already have export to
      CSV, which is readily available for import to Excel.
- [ ] **Pause mid-step**, in both single-step and Run all mode.  The pump
      does not support this well: it has no pause command, and stopping a
      channel loses its count of how much it has pumped. Resuming would
      have to estimate the delivered volume (flow rate × running time) and
      restart each channel with the remaining volume.
- [ ] **Show total volume pumped** for each channel
- [ ] **Optional labels** for channels should be part of presets
- [ ] **Reopen a specific session** and add to it if finished prematurely.
