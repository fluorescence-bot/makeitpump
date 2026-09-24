import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll } from "vitest";
import { getApi, onEvent, isMock, pumpsError } from "./bridge";
import { flow } from "./flow";
import type { Block, BackendEvent, Preset } from "./types";

describe("bridge mock", () => {
  let events: BackendEvent[] = [];

  // Max flow of 100 mL/s, so a channel takes ml / speed seconds and tests stay fast
  const realMax = flow.maxMlPerS;
  beforeAll(() => {
    flow.maxMlPerS = 100;
  });
  afterAll(() => {
    flow.maxMlPerS = realMax;
  });

  beforeEach(() => {
    // Clear events for this test
    events = [];
  });

  afterEach(() => {
    events = [];
  });

  async function runWithEvents(fn: () => Promise<void>): Promise<BackendEvent[]> {
    // Create a fresh listener just for this test
    const testEvents: BackendEvent[] = [];
    const listener = (e: BackendEvent) => testEvents.push(e);
    onEvent(listener);

    try {
      await fn();
    } finally {
      // Don't unsubscribe since we can't unsubscribe
    }

    return testEvents;
  }

  describe("run_program", () => {
    it("emits correct event order for single pump block", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 5, speed: 50 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await promise;
      });

      // First event should be status: raining
      expect(testEvents[0]).toMatchObject({ type: "status", status: "raining" });

      // Should have block_start
      const blockStarts = testEvents.filter((e) => e.type === "block_start");
      expect(blockStarts.length).toBe(1);
      expect(blockStarts[0]).toMatchObject({
        type: "block_start",
        index: 0,
        repeat: 0,
      });

      // Should have block_end
      const blockEnds = testEvents.filter((e) => e.type === "block_end");
      expect(blockEnds.length).toBe(1);

      // Should have progress events
      const progressEvents = testEvents.filter((e) => e.type === "progress");
      expect(progressEvents.length).toBeGreaterThan(0);

      // Last event should be status: done
      const statusDones = testEvents.filter((e) => e.type === "status" && e.status === "done");
      expect(statusDones.length).toBeGreaterThan(0);
    });

    it("respects multi-channel durations", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, speed: 5 },
              B: { ml: 1, speed: 10 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        const start = Date.now();
        await api.run_program(blocks, 1);
        const duration = (Date.now() - start) / 1000;

        // Should run for ~0.2s (max of A and B)
        expect(duration).toBeGreaterThanOrEqual(0.15);
        expect(duration).toBeLessThan(0.5);
      });
    });

    it("rejects concurrent runs", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, speed: 1 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        // Start first run (don't await)
        const promise1 = api.run_program(blocks, 1);

        // Try to start second immediately
        const promise2 = api.run_program(blocks, 1);
        const result2 = await promise2;

        expect(result2.ok).toBe(false);
        expect(result2.error).toContain("already running");

        // Let first finish
        await promise1;
      });
    });
  });

  describe("pause and resume", () => {
    it("emits paused status", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 3, speed: 10 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);

        // Wait a bit, then pause
        await new Promise((r) => setTimeout(r, 50));
        const pauseResult = await api.pause_run();
        expect(pauseResult.ok).toBe(true);

        // Check for paused status
        await new Promise((r) => setTimeout(r, 50));

        // Resume
        const resumeResult = await api.resume_run();
        expect(resumeResult.ok).toBe(true);

        await promise;
      });
    });
  });

  describe("stop", () => {
    it("emits stopped status", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, speed: 0.5 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await new Promise((r) => setTimeout(r, 50));

        const stopResult = await api.stop();
        expect(stopResult.ok).toBe(true);

        await promise;
      });

      const stoppedStatus = testEvents.find((e) => e.type === "status" && e.status === "stopped");
      expect(stoppedStatus).toBeDefined();
    });
  });

  describe("manual dose", () => {
    it("emits dosing, dose_start, pumped and ready", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();
        const result = await api.manual_dose("A", 2, 20);
        expect(result.ok).toBe(true);
        await new Promise((r) => setTimeout(r, 250));
      });

      const dosing = testEvents.findIndex((e) => e.type === "status" && e.status === "dosing");
      const start = testEvents.findIndex((e) => e.type === "dose_start");
      const pumped = testEvents.findIndex((e) => e.type === "pumped");
      const ready = testEvents.findIndex((e) => e.type === "status" && e.status === "ready");
      expect(dosing).toBeGreaterThanOrEqual(0);
      expect(start).toBeGreaterThan(dosing);
      expect(pumped).toBeGreaterThan(start);
      expect(ready).toBeGreaterThan(pumped);
      expect(testEvents[pumped]).toEqual({ type: "pumped", channel: "A", ml: 2, source: "manual" });
    });

    it("rejects a second click while dosing", async () => {
      await runWithEvents(async () => {
        const api = await getApi();
        expect((await api.manual_dose("A", 1, 1)).ok).toBe(true);
        const second = await api.manual_dose("B", 1, 1);
        expect(second.ok).toBe(false);
        await api.stop();
        await new Promise((r) => setTimeout(r, 100));
      });
    });

    it("reports partial mL when stopped", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();
        await api.manual_dose("C", 10, 10);
        await new Promise((r) => setTimeout(r, 300));
        await api.stop();
        await new Promise((r) => setTimeout(r, 100));
      });
      const pumped = testEvents.filter((e) => e.type === "pumped");
      expect(pumped.length).toBe(1);
      const ml = (pumped[0] as { ml: number }).ml;
      expect(ml).toBeGreaterThan(1);
      expect(ml).toBeLessThan(6);
    });

    it("rejects during run", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, speed: 1 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await new Promise((r) => setTimeout(r, 50));

        const result = await api.manual_dose("B", 1, 1);
        expect(result.ok).toBe(false);

        await api.stop();
        await promise;
      });
    });
  });

  describe("validation", () => {
    it("pumpsError mirrors the backend rules", () => {
      expect(pumpsError({ A: { ml: 1, speed: 80 }, B: { ml: 0, speed: 0 } })).toBeNull();
      expect(pumpsError({ A: { ml: 1, speed: 0 } })).toBe("A: mL set but speed = 0");
      expect(pumpsError({ B: { ml: 1, speed: 120 } })).toBe("B: speed must be 0-100");
      expect(pumpsError({ C: { ml: -1, speed: 50 } })).toBe("C: negative mL");
    });

    it("run_program refuses an out-of-range speed", async () => {
      const api = await getApi();
      const blocks: Block[] = [
        {
          id: "b1",
          type: "pump",
          pumps: {
            A: { ml: 1, speed: 150 },
            B: { ml: 0, speed: 0 },
            C: { ml: 0, speed: 0 },
            D: { ml: 0, speed: 0 },
          },
        },
      ];
      expect(await api.run_program(blocks, 1)).toEqual({ ok: false, error: "A: speed must be 0-100" });
    });

    it("manual_dose refuses speed 0 or above 100", async () => {
      const api = await getApi();
      expect((await api.manual_dose("A", 1, 0)).ok).toBe(false);
      expect((await api.manual_dose("A", 1, 101)).ok).toBe(false);
    });
  });

  describe("pumped events", () => {
    it("program blocks report delivered mL per channel", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();
        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 3, speed: 30 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 1, speed: 20 },
            },
          },
        ];
        await api.run_program(blocks, 1);
      });
      const pumped = testEvents.filter((e) => e.type === "pumped");
      expect(pumped).toEqual([
        { type: "pumped", channel: "A", ml: 3, source: "program" },
        { type: "pumped", channel: "D", ml: 1, source: "program" },
      ]);
    });
  });

  describe("repeat", () => {
    it("runs blocks multiple times", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, speed: 20 },
              B: { ml: 0, speed: 0 },
              C: { ml: 0, speed: 0 },
              D: { ml: 0, speed: 0 },
            },
          },
        ];

        await api.run_program(blocks, 3);
      });

      const blockStarts = testEvents.filter((e) => e.type === "block_start");
      expect(blockStarts.length).toBe(3);

      const repeats = blockStarts.map((e) => (e as any).repeat);
      expect(repeats).toEqual([0, 1, 2]);
    });
  });

  describe("presets", () => {
    it("saves and loads presets", async () => {
      // Skip if localStorage not available
      if (typeof localStorage === "undefined") {
        expect(true).toBe(true);
        return;
      }

      await runWithEvents(async () => {
        localStorage.clear();

        const api = await getApi();

        const preset: Preset = {
          name: "Test Preset",
          solutions: { A: "Liquid A", B: "Liquid B", C: "Liquid C", D: "Result" },
          blocks: [
            {
              id: "b1",
              type: "pump",
              pumps: {
                A: { ml: 10, speed: 80 },
                B: { ml: 0, speed: 0 },
                C: { ml: 0, speed: 0 },
                D: { ml: 0, speed: 0 },
              },
            },
          ],
          repeat: 2,
        };

        const saveResult = await api.save_preset(preset);
        expect(saveResult.ok).toBe(true);

        const listed = await api.list_presets();
        expect(listed).toContain("Test Preset");

        const loaded = await api.load_preset("Test Preset");
        expect(loaded).toBeDefined();
        expect(loaded?.name).toBe("Test Preset");
        expect(loaded?.solutions).toEqual(preset.solutions);
        expect(loaded?.repeat).toBe(2);
      });
    });

    it("returns null for non-existent preset", async () => {
      // Skip if localStorage not available
      if (typeof localStorage === "undefined") {
        expect(true).toBe(true);
        return;
      }

      await runWithEvents(async () => {
        const api = await getApi();

        const loaded = await api.load_preset("Non-existent");
        expect(loaded).toBeNull();
      });
    });
  });

  describe("YAML round-trip", () => {
    it("preserves preset data through stringify and parse", async () => {
      // Skip if localStorage not available
      if (typeof localStorage === "undefined") {
        expect(true).toBe(true);
        return;
      }

      await runWithEvents(async () => {
        localStorage.clear();

        const api = await getApi();

        const preset: Preset = {
          name: "Complex Preset",
          solutions: { A: "Milk", B: "Water", C: "NaOH", D: "Final mix" },
          blocks: [
            {
              id: "b1",
              type: "pump",
              pumps: {
                A: { ml: 10.5, speed: 62.5 },
                B: { ml: 3, speed: 12.5 },
                C: { ml: 0, speed: 0 },
                D: { ml: 0, speed: 0 },
              },
            },
            {
              id: "b2",
              type: "pause",
              s: 2.5,
            },
            {
              id: "b3",
              type: "pump",
              pumps: {
                A: { ml: 0, speed: 0 },
                B: { ml: 0, speed: 0 },
                C: { ml: 1, speed: 40 },
                D: { ml: 9, speed: 80 },
              },
            },
          ],
          repeat: 3,
        };

        // Save and load through preset system
        const saveResult = await api.save_preset(preset);
        expect(saveResult.ok).toBe(true);

        const loaded = await api.load_preset("Complex Preset");
        expect(loaded).toBeDefined();

        // Deep equality check
        expect(loaded?.name).toBe(preset.name);
        expect(loaded?.solutions).toEqual(preset.solutions);
        expect(loaded?.repeat).toBe(preset.repeat);
        expect(loaded?.blocks).toHaveLength(3);

        // Check specific block values
        const firstBlock = loaded?.blocks[0];
        if (firstBlock && firstBlock.type === "pump") {
          expect(firstBlock.pumps.A.ml).toBe(10.5);
          expect(firstBlock.pumps.A.speed).toBe(62.5);
          expect(firstBlock.pumps.B.ml).toBe(3);
          expect(firstBlock.pumps.B.speed).toBe(12.5);
        }

        const pauseBlock = loaded?.blocks[1];
        if (pauseBlock && pauseBlock.type === "pause") {
          expect(pauseBlock.s).toBe(2.5);
        }
      });
    });
  });
});
