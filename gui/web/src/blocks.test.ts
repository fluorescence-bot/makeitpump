import { describe, it, expect } from "vitest";
import { normalizePumpBlock } from "./blocks";
import type { PumpBlock } from "./types";

describe("normalizePumpBlock", () => {
  it("normalizes new format with all channels", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, s: 5 },
        B: { ml: 5, s: 3 },
        C: { ml: 2, s: 1 },
        D: { ml: 1, s: 2 },
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.id).toBe("b1");
    expect(result.type).toBe("pump");
    expect(result.pumps.A).toEqual({ ml: 10, s: 5 });
    expect(result.pumps.B).toEqual({ ml: 5, s: 3 });
    expect(result.pumps.C).toEqual({ ml: 2, s: 1 });
    expect(result.pumps.D).toEqual({ ml: 1, s: 2 });
  });

  it("fills missing channels with zeros", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, s: 5 },
        B: { ml: 5, s: 3 },
        // C and D missing
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.A).toEqual({ ml: 10, s: 5 });
    expect(result.pumps.B).toEqual({ ml: 5, s: 3 });
    expect(result.pumps.C).toEqual({ ml: 0, s: 0 });
    expect(result.pumps.D).toEqual({ ml: 0, s: 0 });
  });

  it("handles invalid values by defaulting to zero", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: "invalid" as any, s: NaN },
        B: { ml: -5, s: 3 },
        C: { ml: 2, s: undefined as any },
        D: { ml: null as any, s: 0 },
      },
    };

    const result = normalizePumpBlock(block);

    // Non-number values default to 0; negative numbers are preserved (validation elsewhere)
    expect(result.pumps.A).toEqual({ ml: 0, s: 0 });
    expect(result.pumps.B).toEqual({ ml: -5, s: 3 }); // negative values are preserved
    expect(result.pumps.C).toEqual({ ml: 2, s: 0 });
    expect(result.pumps.D).toEqual({ ml: 0, s: 0 });
  });

  it("converts legacy single-channel format", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      channel: "A",
      ml: 15,
      s: 8,
    };

    const result = normalizePumpBlock(block);

    expect(result.id).toBe("b1");
    expect(result.type).toBe("pump");
    expect(result.pumps.A).toEqual({ ml: 15, s: 8 });
    expect(result.pumps.B).toEqual({ ml: 0, s: 0 });
    expect(result.pumps.C).toEqual({ ml: 0, s: 0 });
    expect(result.pumps.D).toEqual({ ml: 0, s: 0 });
  });

  it("handles legacy format for channel D", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      channel: "D",
      ml: 20,
      s: 10,
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.D).toEqual({ ml: 20, s: 10 });
    expect(result.pumps.A).toEqual({ ml: 0, s: 0 });
  });

  it("preserves negative values in legacy format (for validation elsewhere)", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      channel: "B",
      ml: -5,
      s: 3,
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.B.ml).toBe(-5); // negative values are preserved for validation elsewhere
    expect(result.pumps.B.s).toBe(3);
  });

  it("handles mixed valid and invalid values", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, s: 5 },
        B: undefined as any,
        C: { ml: 2, s: 1 },
        D: null as any,
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.A).toEqual({ ml: 10, s: 5 });
    expect(result.pumps.B).toEqual({ ml: 0, s: 0 });
    expect(result.pumps.C).toEqual({ ml: 2, s: 1 });
    expect(result.pumps.D).toEqual({ ml: 0, s: 0 });
  });

  it("handles empty pumps object", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {},
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps).toEqual({
      A: { ml: 0, s: 0 },
      B: { ml: 0, s: 0 },
      C: { ml: 0, s: 0 },
      D: { ml: 0, s: 0 },
    });
  });

  it("ignores legacy format if new format exists", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, s: 5 },
        B: { ml: 0, s: 0 },
        C: { ml: 0, s: 0 },
        D: { ml: 0, s: 0 },
      },
      channel: "B", // This should be ignored
      ml: 999,
      s: 999,
    };

    const result = normalizePumpBlock(block);

    // New format should take precedence
    expect(result.pumps.A).toEqual({ ml: 10, s: 5 });
    expect(result.pumps.B).toEqual({ ml: 0, s: 0 }); // Not 999
  });

  it("returns correct PumpBlock type", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 5, s: 2 },
        B: { ml: 0, s: 0 },
        C: { ml: 0, s: 0 },
        D: { ml: 0, s: 0 },
      },
    };

    const result: PumpBlock = normalizePumpBlock(block);

    expect(result).toBeDefined();
    expect(result.type).toBe("pump");
  });
});
