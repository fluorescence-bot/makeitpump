"""
Hardware stubs for RainMixer pump control.

The backend team replaces these functions with actual pump control logic.
Each function here just prints what it would do.
"""

# Flow of one channel at speed 100 %. Placeholder until the pump is calibrated;
# the runner uses it to know how long a dose takes. Must match flow.ts in the web UI.
MAX_FLOW_ML_PER_S = 1.0


def start_pump(channel: str, ml: float, speed: float) -> None:
    """Start a pump on the given channel to deliver `ml` at `speed` (0-100 % of max flow)."""
    print(f"[hardware] start_pump {channel} {ml} mL at {speed} %")


def stop_pump(channel: str) -> None:
    """Stop the pump on the given channel."""
    print(f"[hardware] stop_pump {channel}")


def stop_all() -> None:
    """Stop all pumps immediately."""
    print(f"[hardware] stop_all")
