// The pump runs every channel at its own rate (the DilutionController uses 90 % of
// its max flow). The UI and the mock need a duration for animations, block times and
// the demo runner, so they estimate it with the same flow as the Python runner
// (hardware.FLOW_ML_PER_S). Placeholder until calibrated.
export const flow = { mlPerS: 1 };

/** Estimated seconds one channel takes to pump `ml`. 0 if the channel is idle. */
export function pumpSeconds(ml: number): number {
  if (ml <= 0) return 0;
  return ml / flow.mlPerS;
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

// Flushing and cleaning: mirrors flush.py (a test checks the constants match).
// An empty tube has to fill before liquid comes out, so a flush pumps the tube's
// volume plus FLUSH_EXCESS_ML. Never counted in the totals.
export const TUBE_INNER_DIAMETER_MM = 2.79;
export const FLUSH_EXCESS_ML = 3.0;
export const CLEAN_ROUNDS = 3;
export const MAX_TUBE_LENGTH_MM = 10000;

/** What a tube of `lengthMm` holds, in mL. */
export function tubeVolumeMl(lengthMm: number): number {
  const r = TUBE_INNER_DIAMETER_MM / 2;
  return (Math.PI * r * r * lengthMm) / 1000;
}

/** How much to pump to fill a tube of `lengthMm` and clear it. */
export function flushMl(lengthMm: number): number {
  return tubeVolumeMl(lengthMm) + FLUSH_EXCESS_ML;
}

/** Why a tube length isn't valid, or null (mirrors flush.tube_length_error). */
export function tubeLengthError(lengthMm: number): string | null {
  if (!Number.isFinite(lengthMm)) return "Tube length must be a number of mm";
  if (!(lengthMm > 0 && lengthMm <= MAX_TUBE_LENGTH_MM)) return `Tube length must be more than 0 and at most ${MAX_TUBE_LENGTH_MM} mm`;
  return null;
}

export interface CleanPlan {
  steps: [number, number, number, number][];   // mL on A, B, C, D per pump step, in order
  waterMl: [number, number, number];           // water each beaker A-C needs
  seconds: number;                             // estimated total time
}

/** A clean (mirrors flush.clean_plan): each round A-C rinse their flush volume
 *  into the bowl, then D pumps all of it plus its own flush volume out. */
export function cleanPlan(lengthsMm: [number, number, number, number]): CleanPlan {
  const [f1, f2, f3, f4] = lengthsMm.map(flushMl);
  const rinse: [number, number, number, number] = [f1, f2, f3, 0];
  const drain: [number, number, number, number] = [0, 0, 0, f1 + f2 + f3 + f4];
  const steps: [number, number, number, number][] = [];
  for (let i = 0; i < CLEAN_ROUNDS; i++) steps.push(rinse, drain);
  const seconds = steps.reduce((sum, step) => sum + pumpSeconds(Math.max(...step)), 0);
  return { steps, waterMl: [CLEAN_ROUNDS * f1, CLEAN_ROUNDS * f2, CLEAN_ROUNDS * f3], seconds };
}
