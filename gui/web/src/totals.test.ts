import { describe, it, expect } from "vitest";
import { computeTotals } from "./totals";
import type { Block } from "./types";

describe("computeTotals", () => {
  it("computes totals for a simple pump block", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, s: 5 },
          B: { ml: 5, s: 3 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 1);

    expect(result.totalIn).toBe(15); // 10 + 5 + 0
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(5); // max(5, 3, 0, 0)
    expect(result.timeStr).toBe("0:05");
  });

  it("computes totals with D channel (output)", () => {
    const blocks: Block[] = [
      {
        id: "b1",
        type: "pump",
        pumps: {
          A: { ml: 10, s: 5 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 8, s: 10 },
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
          A: { ml: 5, s: 2 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
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
          A: { ml: 10, s: 6 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
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
          A: { ml: 10, s: 2 },
          B: { ml: 5, s: 2 },
          C: { ml: 0, s: 0 },
          D: { ml: 3, s: 2 },
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
          A: { ml: 1, s: 65 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
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
          A: { ml: 5, s: 2 },
          B: { ml: 3, s: 3 },
          C: { ml: 2, s: 1 },
          D: { ml: 1, s: 2 },
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
          A: { ml: 4, s: 1 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 5, s: 2 },
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
          A: { ml: 0, s: 0 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
        },
      },
    ];

    const result = computeTotals(blocks, 5);

    expect(result.totalIn).toBe(0);
    expect(result.totalOut).toBe(0);
    expect(result.totalTime).toBe(0);
  });
});
