"""Driver mock classes for offline testing.

Distributed under the GNU General Public License v3
Copyright (C) 2022 NuMat Technologies
"""

from unittest.mock import MagicMock

from ismatec.driver import Pump as RealPump
from ismatec.util import Mode, Rotation, Setpoint, Tubing, pack_time1, pack_volume1

import asyncio
import threading
import time

SCALING_FACTOR = 0.1

class AsyncClientMock(MagicMock):
    """Magic mock that works with async methods."""

    async def __call__(self, *args, **kwargs):
        """Convert regular mocks into into an async coroutine."""
        return super().__call__(*args, **kwargs)


class Pump(RealPump):
    """Mocks the overhead stirrer driver for offline testing."""

    def __init__(self, *args, **kwargs):
        """Set up connection parameters with default port."""
        self.client = AsyncClientMock()
        self.channels = [1, 2, 3, 4]
        self.running = {channel: False for channel in self.channels}
        self.state = {
            'channel_addressing': False,
            'event_messaging': False,
            'channels': [
                {
                    'flowrate': 1.0 * c,
                    'rotation': Rotation.CLOCKWISE,
                    'mode': Mode.RPM,
                    'diameter': Tubing[0],
                    'setpoint_type': Setpoint.RPM,
                    'rpm': 0.0,
                    'volume': 0.0,
                    'cycles': 0,
                    'runtime': 0.0,
                }
                for c in self.channels
            ],
        }
        self.hw = Communicator()
        self.hw.channels = self.channels
        self.hw.running = self.running
        self.hw.state = self.state

        # Wire the mock's "unsolicited" start/stop events back into this Pump,
        # mirroring what SerialCommunicator.loop() does for real hardware
        # (the '^U'/'^X' lines -> running_callback).
        self.hw.on_start = lambda ch: self._set_running_status(True, ch)
        self.hw.on_stop = self._handle_stopped

        self._loop = asyncio.get_event_loop()
        self._done_events = {ch: asyncio.Event() for ch in self.channels}

    def _handle_stopped(self, channel):
        """Called from the background pumping thread when it finishes."""
        self._set_running_status(False, channel)
        # We're on a plain threading.Thread here, not the event loop, so
        # flip the asyncio.Event in a thread-safe way.
        self._loop.call_soon_threadsafe(self._done_events[channel].set)

    async def __aenter__(self, *args):
        return self

    async def __aexit__(self, *args):
        pass

    async def dispense_volume(self, channel: int, vol: float, rate: float, units='ml/min'):
        """Dispense `vol` mL at `rate`, and wait until it has ACTUALLY finished.

        Use this instead of `dispense_vol_at_rate` when you need to tell the
        user "X ml has been pumped" only once it's true.
        """
        self._done_events[channel].clear()
        await self.dispense_vol_at_rate(channel, vol, rate, units=units)
        await self._done_events[channel].wait()
        return {"channel": channel, "volume_ml": vol}


class Communicator(MagicMock):
    """Mock the pump communication hardware."""

    def query(self, command) -> str:  # noqa: C901
        """Mock replies to queries."""
        if command == '1~':  # channel addressing
            return '1' if self.state['channel_addressing'] else '0'
        channel = int(command[0])
        if channel not in self.channels:
            raise ValueError
        command = command[1:]
        if command == 'f':  # getFlowrate (in mL/min)
            return pack_volume1(self.state['channels'][channel - 1]['flowrate'])
        elif command == 'xD':  # get rotation direction
            return Rotation[self.state['channels'][channel - 1]['rotation']].value
        elif command == 'xM':  # get current mode
            return Mode[self.state['channels'][channel - 1]['mode']].value
        elif command == 'xf':  # get current setpoint type
            return Setpoint[self.state['channels'][channel - 1]['setpoint_type']].value
        elif command == 'xE':  # async event messages enabled?
            return '1' if self.state['event_messaging'] else '0'
        elif command == 'x!':  # protocol version
            return '8'
        elif command == '+':  # tubing diameter
            return str(self.state['channels'][channel - 1]['diameter']) + ' mm'
        elif command == 'S':  # get speed (RPM)
            return str(self.state['channels'][channel - 1]['rpm'])
        elif command == 'v':  # get volume (mL)
            return str(round(self.state['channels'][channel - 1]['volume'] * 100, 2)) + 'E+1'
        elif command.startswith('xT'):  # get runtime (in min)
            return pack_time1(self.state['channels'][channel - 1]['runtime'], units='m')
        elif command == '#':  # pump version
            return 'REGLO ICC 0208 306'
        elif command == 'xe':
            return self._get_cannot_run_response(channel)
        elif command in ['!', '?']:
            return ('0.281 ml/min' if self.state['channels'][channel - 1]['diameter'] == 0.19
                    else '0.138 ml/min')
        elif command.startswith('f'):  # SET flowrate — register only, no motion yet
            self.state['channels'][channel - 1]['flowrate'] = self._decode_packed(command)
            return '*'
        elif command.startswith('v'):  # SET volume — register only, no motion yet
            self.state['channels'][channel - 1]['volume'] = self._decode_packed(command)
            return '*'
        elif command.startswith('E'): # Returns whether the pump is currently running or not. + = running, - = stopped
            return '+' if any(self.pump.running.values) else '-'
        else:
            raise NotImplementedError

    @staticmethod
    def _decode_packed(command: str) -> float:
        """Decode a 'volume type 2' packed value, e.g. 'f5000+1' -> 50.0."""
        mantissa = float(command[1:5]) / 1000
        exponent = int(command[-2:])
        return mantissa * 10 ** exponent

    def _get_cannot_run_response(self, channel):
        """Return the responses for when the pump failed to run."""
        if (self.state['channels'][channel - 1]['mode'] == Mode.VOL_PAUSE.name
           and self.state['channels'][channel - 1]['cycles'] == 0):
            return 'C 0000E+1'  # cycles = 0
        elif (self.state['channels'][channel - 1]['mode'] == Mode.FLOWRATE.name
              and self.state['channels'][channel - 1]['flowrate'] == 0):
            return 'R 1386E-1'  # flowrate = 0
        elif (self.state['channels'][channel - 1]['mode'].startswith('VOL_')
              and self.state['channels'][channel - 1]['volume'] >= 1256):
            return 'V 8308E+3'  # flowrate > max
        return ValueError

    def command(self, command):  # noqa: C901
        """Mock commands."""
        if command == '10':  # reset all settings
            for channel, _ in enumerate(self.channels):
                self.state['channels'][channel]['rotation'] = Rotation.CLOCKWISE.name
                self.state['channels'][channel]['flowrate'] = 0.0
                self.running[channel] = False
            return True
        elif command.startswith('1~'):
            self.state['channel_addressing'] = bool(int(command[-1]))
            return True
        channel = int(command[0])
        if channel not in self.channels:
            raise ValueError
        command = command[1:]
        if command in ['J', 'K']:  # set to CCW (K) or CW (J) rotation
            self.state['channels'][channel - 1]['rotation'] = Rotation(command).name
        elif command.startswith('xE'):
            self.state['event_messaging'] = bool(int(command[-1]))
        elif command == 'H':  # start
            if self._check_pump_will_run(channel):
                self.running[channel] = True
                vol = self.state['channels'][channel - 1]['volume']
                rate = self.state['channels'][channel - 1]['flowrate']
                if vol and rate:
                    self._simulate_dispense(channel, vol, rate)   # blocks until done
                elif self.on_start:
                    self.on_start(channel)  # continuous-flow run, no auto-stop
            else:
                return False
        elif command == 'I':  # stop
            self.running[channel] = False
        elif command in [m.value for m in Mode]:
            self.state['channels'][channel - 1]['mode'] = Mode(command).name
        elif command.startswith('xf'):
            self.state['channels'][channel - 1]['setpoint_type'] = Setpoint(command[-1]).name
        elif command.startswith('+'):
            self.state['channels'][channel - 1]['diameter'] = float(command[1:]) / 100
        elif command.startswith('S'):
            self.state['channels'][channel - 1]['rpm'] = float(command[1:]) / 100
        elif command.startswith('f'):
            self.state['channels'][channel - 1]['flowrate'] = self._decode_packed(command)
        elif command.startswith('v'):
            self.state['channels'][channel - 1]['volume'] = self._decode_packed(command)
        elif command.startswith('xT'):
            seconds = float(command[3:]) / 10
            self.state['channels'][channel - 1]['runtime'] = seconds / 60
        else:
            raise NotImplementedError
        return True

    def _simulate_dispense(self, channel, vol_ml, rate_ml_per_min):
        """Runs in a background thread — this is the actual 'mock pumping'."""
        if self.on_start:
            self.on_start(channel)
        print(f"Pumping on channel {channel}: {vol_ml} ml at {rate_ml_per_min} ml/min")
        pump_time = abs(vol_ml / rate_ml_per_min) * SCALING_FACTOR
        time.sleep(pump_time)
        print(f"Done with channel {channel}")
        if self.on_stop:
            self.on_stop(channel)

    def _check_pump_will_run(self, channel):
        """Return if the pump will run with the current settings."""
        return not ((self.state['channels'][channel - 1]['mode'] == Mode.VOL_PAUSE.name
                    and self.state['channels'][channel - 1]['cycles'] == 0)
                    or (self.state['channels'][channel - 1]['mode'] == Mode.FLOWRATE.name
                    and self.state['channels'][channel - 1]['flowrate'] == 0)
                    or (self.state['channels'][channel - 1]['mode'].startswith('VOL_')
                    and self.state['channels'][channel - 1]['volume'] >= 1256))
