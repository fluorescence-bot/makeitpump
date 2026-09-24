import { describe, it, expect } from "vitest";
import { normalizePumpBlock } from "./blocks";
import { DEFAULT_SPEED } from "./flow";
import type { PumpBlock } from "./types";

const idle = { ml: 0, speed: DEFAULT_SPEED };

describe("normalizePumpBlock", () => {
  it("normalizes new format with all channels", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, speed: 80 },
        B: { ml: 5, speed: 50 },
        C: { ml: 2, speed: 10 },
        D: { ml: 1, speed: 100 },
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.id).toBe("b1");
    expect(result.type).toBe("pump");
    expect(result.pumps.A).toEqual({ ml: 10, speed: 80 });
    expect(result.pumps.B).toEqual({ ml: 5, speed: 50 });
    expect(result.pumps.C).toEqual({ ml: 2, speed: 10 });
    expect(result.pumps.D).toEqual({ ml: 1, speed: 100 });
  });

  it("fills missing channels with 0 mL at the default speed", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, speed: 80 },
        B: { ml: 5, speed: 50 },
        // C and D missing
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.A).toEqual({ ml: 10, speed: 80 });
    expect(result.pumps.B).toEqual({ ml: 5, speed: 50 });
    expect(result.pumps.C).toEqual(idle);
    expect(result.pumps.D).toEqual(idle);
  });

  it("gives presets saved with seconds the default speed", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, s: 1 },
        B: { ml: 0, s: 0 },
      },
    };

    const result = normalizePumpBlock(block);

    // ml/s would be 1000 %, so the old seconds are dropped rather than converted
    expect(result.pumps.A).toEqual({ ml: 10, speed: DEFAULT_SPEED });
    expect(result.pumps.B).toEqual(idle);
    expect(result.pumps.A).not.toHaveProperty("s");
  });

  it("handles invalid values", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: "invalid" as any, speed: NaN },
        B: { ml: -5, speed: 150 },
        C: { ml: 2, speed: undefined as any },
        D: { ml: null as any, speed: 0 },
      },
    };

    const result = normalizePumpBlock(block);

    // Non-numbers fall back to defaults; out-of-range numbers are preserved (validation elsewhere)
    expect(result.pumps.A).toEqual(idle);
    expect(result.pumps.B).toEqual({ ml: -5, speed: 150 });
    expect(result.pumps.C).toEqual({ ml: 2, speed: DEFAULT_SPEED });
    expect(result.pumps.D).toEqual({ ml: 0, speed: 0 });
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
    expect(result.pumps.A).toEqual({ ml: 15, speed: DEFAULT_SPEED });
    expect(result.pumps.B).toEqual(idle);
    expect(result.pumps.C).toEqual(idle);
    expect(result.pumps.D).toEqual(idle);
  });

  it("handles legacy format for channel D with a speed", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      channel: "D",
      ml: 20,
      speed: 30,
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.D).toEqual({ ml: 20, speed: 30 });
    expect(result.pumps.A).toEqual(idle);
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

    expect(result.pumps.B.ml).toBe(-5);
    expect(result.pumps.B.speed).toBe(DEFAULT_SPEED);
  });

  it("handles mixed valid and invalid values", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, speed: 80 },
        B: undefined as any,
        C: { ml: 2, speed: 10 },
        D: null as any,
      },
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps.A).toEqual({ ml: 10, speed: 80 });
    expect(result.pumps.B).toEqual(idle);
    expect(result.pumps.C).toEqual({ ml: 2, speed: 10 });
    expect(result.pumps.D).toEqual(idle);
  });

  it("handles empty pumps object", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {},
    };

    const result = normalizePumpBlock(block);

    expect(result.pumps).toEqual({ A: idle, B: idle, C: idle, D: idle });
  });

  it("ignores legacy format if new format exists", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 10, speed: 80 },
        B: { ml: 0, speed: 80 },
        C: { ml: 0, speed: 80 },
        D: { ml: 0, speed: 80 },
      },
      channel: "B", // This should be ignored
      ml: 999,
      speed: 99,
    };

    const result = normalizePumpBlock(block);

    // New format should take precedence
    expect(result.pumps.A).toEqual({ ml: 10, speed: 80 });
    expect(result.pumps.B).toEqual({ ml: 0, speed: 80 }); // Not 999
  });

  it("returns correct PumpBlock type", () => {
    const block = {
      id: "b1",
      type: "pump" as const,
      pumps: {
        A: { ml: 5, speed: 80 },
        B: { ml: 0, speed: 80 },
        C: { ml: 0, speed: 80 },
        D: { ml: 0, speed: 80 },
      },
    };

    const result: PumpBlock = normalizePumpBlock(block);

    expect(result).toBeDefined();
    expect(result.type).toBe("pump");
  });
});
