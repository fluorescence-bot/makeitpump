import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { computeTotals } from "./totals";
import { flow } from "./flow";
import type { Block } from "./types";

describe("computeTotals", () => {
  // Max flow of 100 mL/s, so a channel takes ml / speed seconds
  const realMax = flow.maxMlPerS;
  beforeAll(() => {
    flow.maxMlPerS = 100;
  });
  afterAll(() => {
    flow.maxMlPerS = realMax;
  });

  it("takes longer at a lower speed", () => {
    const block = (speed: number): Block[] => [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, speed },
          B: { ml: 0, speed: 80 },
          C: { ml: 0, speed: 80 },
          D: { ml: 0, speed: 80 },
        },
      },
    ];
    expect(computeTotals(block(10), 1).totalTime).toBe(1);
    expect(computeTotals(block(5), 1).totalTime).toBe(2);
    expect(computeTotals(block(0), 1).totalTime).toBe(0); // speed 0 = idle
  });

  it("computes totals for a simple pump block", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, speed: 2 },
          B: { ml: 5, speed: 1.67 },
          C: { ml: 0, speed: 0 },
          D: { ml: 0, speed: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.totalIn).toBe(15); // 10 + 5 + 0
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(5); // slowest channel: A 10 mL at 2 % of 100 mL/s
    expect(result.timeStr).toBe("0:05");
  });

  it("computes totals with D channel (output)", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, speed: 2 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 8, speed: 0.8 },
        },
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.totalIn).toBe(10); // A only
    expect(result.totalOut).toBe(8); // D only
    expect(result.totalTime).toBe(10); // max of all
  });

  it("computes totals with pause blocks", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 5, speed: 2.5 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 0, speed: 0 },
        },
      },
      {
        id: "b2",
        type: "pause",
        s: 3,
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.totalIn).toBe(5);
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(5); // 2 + 3
  });

  it("multiplies totalTime by repeat", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 12, speed: 2 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 0, speed: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 3);

    // totalTime is multiplied by repeat
    expect(result.totalTime).toBe(18); // 6 * 3
    expect(result.timeStr).toBe("0:18");
  });

  it("multiplies totalIn/totalOut by repeat too", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, speed: 5 },
          B: { ml: 5, speed: 2.5 },
          C: { ml: 0, speed: 0 },
          D: { ml: 3, speed: 1.5 },
        },
      },
    ];

    const result = computeTotals(blocks, 2);

    // A full run repeats the whole block sequence, so mL totals scale with repeat too
    expect(result.totalIn).toBe(30); // (10 + 5) * 2
    expect(result.totalOut).toBe(6); // 3 * 2
    expect(result.totalTime).toBe(4); // 2 * 2
  });

  it("formats time correctly with minutes and seconds", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 65, speed: 1 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 0, speed: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.timeStr).toBe("1:05"); // 65 seconds = 1 min 5 sec
  });

  it("handles multiple blocks correctly", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 5, speed: 2.5 },
          B: { ml: 3, speed: 1 },
          C: { ml: 2, speed: 2 },
          D: { ml: 1, speed: 0.5 },
        },
      },
      {
        id: "b2",
        type: "pause",
        s: 1,
      },
      {
        id: "b3",
        type: "pump",
        pumps: {
          A: { ml: 4, speed: 4 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 5, speed: 2.5 },
        },
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.totalIn).toBe(5 + 3 + 2 + 4); // A and B and C from b1, A from b3
    expect(result.totalOut).toBe(1 + 5); // D from b1 and b3
    expect(result.totalTime).toBe(3 + 1 + 2); // max(2,3,1,2) + 1 + max(1,0,0,2)
  });

  it("handles empty block list", () => {
    const result = computeTotals([], 1);

    expect(result.totalIn).toBe(0);
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(0);
    expect(result.timeStr).toBe("0:00");
  });

  it("handles blocks with all zeros", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 0, speed: 0 },
          B: { ml: 0, speed: 0 },
          C: { ml: 0, speed: 0 },
          D: { ml: 0, speed: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 5);

    expect(result.totalIn).toBe(0);
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(0);
  });
});
