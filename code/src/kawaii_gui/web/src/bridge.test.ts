import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll } from "vitest";
import { getApi, onEvent, isMock, pumpsError } from "./bridge";
import { flow } from "./flow";
import type { Block, BackendEvent, Preset } from "./types";

describe("bridge mock", () => {
  let events: BackendEvent[] = [];

  // Flow of 10 mL/s, so a channel takes ml / 10 seconds and tests stay fast
  const realFlow = flow.mlPerS;
  beforeAll(() => {
    flow.mlPerS = 10;
  });
  afterAll(() => {
    flow.mlPerS = realFlow;
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
              A: { ml: 5 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 0 },
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
              A: { ml: 2 },
              B: { ml: 1 },
              C: { ml: 0 },
              D: { ml: 0 },
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
              A: { ml: 5 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 0 },
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
    it("pauses after the pump block, not during it", async () => {
      const testEvents: BackendEvent[] = [];
      onEvent((e) => testEvents.push(e));
      const statuses = () =>
        testEvents.filter((e) => e.type === "status").map((e) => (e as { status: string }).status);
      {
        const api = await getApi();

        const idle = { ml: 0 };
        const blocks: Block[] = [
          { id: "b1", type: "pump", pumps: { A: { ml: 3 }, B: idle, C: idle, D: idle } }, // 0.3 s
          { id: "b2", type: "pump", pumps: { A: idle, B: { ml: 1 }, C: idle, D: idle } }, // 0.1 s
        ];

        const promise = api.run_program(blocks, 1);

        // Pause during the first block: it keeps pumping
        await new Promise((r) => setTimeout(r, 50));
        const pauseResult = await api.pause_run();
        expect(pauseResult.ok).toBe(true);
        await new Promise((r) => setTimeout(r, 100));
        expect(statuses()).not.toContain("paused");

        // After the block ends, the run is paused before block 2
        await new Promise((r) => setTimeout(r, 300));
        expect(statuses()).toContain("paused");
        expect(testEvents.filter((e) => e.type === "block_start")).toHaveLength(1);

        const resumeResult = await api.resume_run();
        expect(resumeResult.ok).toBe(true);
        await promise;
      }

      expect(statuses()).toEqual(["raining", "paused", "raining", "done"]);
      expect(testEvents.filter((e) => e.type === "pumped")).toHaveLength(2);
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
              A: { ml: 1 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 0 },
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
        const result = await api.manual_dose("A", 2);
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
        expect((await api.manual_dose("A", 1)).ok).toBe(true);
        const second = await api.manual_dose("B", 1);
        expect(second.ok).toBe(false);
        await api.stop();
        await new Promise((r) => setTimeout(r, 100));
      });
    });

    it("reports partial mL when stopped", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();
        await api.manual_dose("C", 10);
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
              A: { ml: 5 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await new Promise((r) => setTimeout(r, 50));

        const result = await api.manual_dose("B", 1);
        expect(result.ok).toBe(false);

        await api.stop();
        await promise;
      });
    });
  });

  describe("validation", () => {
    it("pumpsError mirrors the backend rules", () => {
      expect(pumpsError({ A: { ml: 1 }, B: { ml: 0 } })).toBeNull();
      expect(pumpsError({ C: { ml: -1 } })).toBe("C: negative mL");
    });

    it("run_program refuses negative mL", async () => {
      const api = await getApi();
      const blocks: Block[] = [
        {
          id: "b1",
          type: "pump",
          pumps: {
            A: { ml: -1 },
            B: { ml: 0 },
            C: { ml: 0 },
            D: { ml: 0 },
          },
        },
      ];
      expect(await api.run_program(blocks, 1)).toEqual({ ok: false, error: "A: negative mL" });
    });

    it("manual_dose refuses 0 or negative mL", async () => {
      const api = await getApi();
      expect((await api.manual_dose("A", 0)).ok).toBe(false);
      expect((await api.manual_dose("A", -1)).ok).toBe(false);
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
              A: { ml: 3 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 1 },
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
              A: { ml: 1 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 0 },
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
                A: { ml: 10 },
                B: { ml: 0 },
                C: { ml: 0 },
                D: { ml: 0 },
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
                A: { ml: 10.5 },
                B: { ml: 3 },
                C: { ml: 0 },
                D: { ml: 0 },
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
                A: { ml: 0 },
                B: { ml: 0 },
                C: { ml: 1 },
                D: { ml: 9 },
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
          expect(firstBlock.pumps.B.ml).toBe(3);
        }

        const pauseBlock = loaded?.blocks[1];
        if (pauseBlock && pauseBlock.type === "pause") {
          expect(pauseBlock.s).toBe(2.5);
        }
      });
    });
  });
});

describe("bridge mock tubing", () => {
  it("reads and sets any tube size in range", async () => {
    const api = await getApi();
    const before = await api.get_tubing();
    expect(before.ok).toBe(true);
    expect(before.min_mm).toBe(0.13);
    expect(before.max_mm).toBe(3.17);

    const after = await api.set_tubing(2.1);
    expect(after.ok).toBe(true);
    expect(after.mm).toBe(2.1);
    expect(after.max_ml_per_min.A).toBeGreaterThan(before.max_ml_per_min.A!);
    expect((await api.set_tubing(1.5)).mm).toBe(1.5);
    expect((await api.set_tubing(1.234)).mm).toBe(1.23);
    await api.set_tubing(before.mm!);
  });

  it("refuses a size outside the range", async () => {
    const api = await getApi();
    const before = await api.get_tubing();
    for (const bad of [0.1, 4, NaN]) {
      const result = await api.set_tubing(bad);
      expect(result.ok).toBe(false);
      expect(result.error).toBe("Tubing must be 0.13-3.17 mm");
      expect(result.mm).toBe(before.mm);
    }
  });
});

describe("bridge mock pump", () => {
  it("always reports the mock pump", async () => {
    const api = await getApi();
    const info = await api.pump_connect();
    expect(info.ok).toBe(true);
    expect(info.mode).toBe("mock");
    expect(info.address).toBeNull();
    expect(info.message).toBe("Demo mode - mock pump");
  });

  it("toggles logging and remembers it", async () => {
    const api = await getApi();
    expect((await api.set_pump_logging(true)).logging).toBe(true);
    expect((await api.pump_connect()).logging).toBe(true);
    expect((await api.set_pump_logging(false)).logging).toBe(false);
  });

  it("has no serial ports, and takes a chosen port like the backend", async () => {
    const api = await getApi();
    expect(await api.list_ports()).toEqual([]);
    expect((await api.pump_connect("COM5")).mode).toBe("mock");
    expect(await api.rotator_connect("COM8")).toEqual({ ok: true });
  });

  it("refuses to redetect while running, but still toggles logging", async () => {
    const api = await getApi();
    const run = api.run_program([{ id: "p1", type: "pause", s: 1 }], 1);
    const busy = await api.pump_connect();
    expect(busy.ok).toBe(false);
    expect(busy.error).toBe("Busy");
    expect(busy.mode).toBe("mock");
    expect((await api.set_pump_logging(true)).ok).toBe(true);
    await api.set_pump_logging(false);
    await api.stop();
    await run;
  });
});
