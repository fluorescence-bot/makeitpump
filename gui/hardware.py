"""
Hardware stubs for RainMixer pump control.

The backend team replaces these functions with actual pump control logic.
Each function here just prints what it would do.
"""


def start_pump(channel: str, ml: float, s: float) -> None:
    """Start a pump on the given channel for the specified volume and duration."""
    print(f"[hardware] start_pump {channel} {ml} mL over {s} s")


def stop_pump(channel: str) -> None:
    """Stop the pump on the given channel."""
    print(f"[hardware] stop_pump {channel}")


def stop_all() -> None:
    """Stop all pumps immediately."""
    print(f"[hardware] stop_all")
