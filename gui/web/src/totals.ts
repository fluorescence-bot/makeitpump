import type { Block, Channel } from "./types";

export interface Totals {
  totalIn: number;
  totalOut: number;
  totalTime: number;
  timeStr: string;
}

/**
 * Compute total input, output, and time for a block sequence with repeat.
 * A run executes the whole block sequence `repeat` times, so totalIn,
 * totalOut and totalTime all scale with repeat.
 */
export function computeTotals(blocks: Block[], repeat: number): Totals {
  let totalIn = 0;
  let totalOut = 0;
  let totalTime = 0;

  blocks.forEach((block) => {
    if (block.type === "pump") {
      // Sum A, B, C for "in"
      totalIn += (block.pumps.A?.ml || 0) + (block.pumps.B?.ml || 0) + (block.pumps.C?.ml || 0);
      // Sum D for "out"
      totalOut += block.pumps.D?.ml || 0;
      // Duration is max(s) across all channels
      const maxS = Math.max(
        block.pumps.A?.s || 0,
        block.pumps.B?.s || 0,
        block.pumps.C?.s || 0,
        block.pumps.D?.s || 0
      );
      totalTime += maxS;
    } else {
      totalTime += block.s;
    }
  });

  const totalTimeWithRepeat = totalTime * repeat;
  const minutes = Math.floor(totalTimeWithRepeat / 60);
  const seconds = Math.round(totalTimeWithRepeat % 60);
  const timeStr = `${minutes}:${seconds.toString().padStart(2, "0")}`;

  return {
    totalIn: totalIn * repeat,
    totalOut: totalOut * repeat,
    totalTime: totalTimeWithRepeat,
    timeStr,
  };
}
