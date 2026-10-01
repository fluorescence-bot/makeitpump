// Pump speed is 0-100 % of the pump's max flow. The UI and the mock need a duration
// for animations, block times and the demo runner, so they convert with the same
// max flow as the Python runner (hardware.MAX_FLOW_ML_PER_S). Placeholder until calibrated.
export const flow = { maxMlPerS: 1 };

// Speed a new pump channel or manual dose starts at.
export const DEFAULT_SPEED = 80;

/** Seconds one channel takes to pump `ml` at `speed` %. 0 if the channel is idle. */
export function pumpSeconds(ml: number, speed: number): number {
  if (ml <= 0 || speed <= 0) return 0;
  return ml / ((flow.maxMlPerS * speed) / 100);
}

// Cuvette rotator: absolute angle in whole degrees, limited by the firmware to 0-60.
export const ROTATE_MAX_DEG = 60;
// Time per degree of rotation, measured on the real board (30° in ~0.9 s).
// Only for animations, block times and the mock.
export const rotate = { sPerDeg: 0.03 };

/** Why a rotate angle is invalid, or null if it is fine (mirrors api.rotate_error). */
export function rotateError(deg: number): string | null {
  if (!Number.isInteger(deg) || deg < 0 || deg > ROTATE_MAX_DEG) return `Rotate: angle must be a whole number 0-${ROTATE_MAX_DEG}°`;
  return null;
}
