import type { Block, Channel } from "./types";
import { pumpSeconds } from "./flow";

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
      // Duration is the slowest channel's time
      const channels: Channel[] = ["A", "B", "C", "D"];
      totalTime += Math.max(...channels.map((ch) => pumpSeconds(block.pumps[ch]?.ml || 0, block.pumps[ch]?.speed || 0)));
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
