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
