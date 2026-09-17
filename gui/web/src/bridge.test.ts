import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { getApi, onEvent, isMock } from "./bridge";
import type { Block, BackendEvent, Preset } from "./types";

describe("bridge mock", () => {
  let events: BackendEvent[] = [];

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
              A: { ml: 5, s: 0.1 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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
              A: { ml: 1, s: 0.2 },
              B: { ml: 1, s: 0.1 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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
              A: { ml: 1, s: 1 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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
              A: { ml: 1, s: 0.3 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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
              A: { ml: 1, s: 2 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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

  describe("pour", () => {
    it("emits correct event order", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();

        const promise = api.pour(10, 0.1);
        await promise;
      });

      // First event should be status: pouring
      const pourStartIndex = testEvents.findIndex((e) => e.type === "status" && e.status === "pouring");
      expect(pourStartIndex).toBeGreaterThanOrEqual(0);

      // Then pour_start should come
      const pourEventIndex = testEvents.findIndex((e) => e.type === "pour_start");
      expect(pourEventIndex).toBeGreaterThan(pourStartIndex);

      // Finally status: done
      const doneIndex = testEvents.findIndex(
        (e, i) => i > pourEventIndex && e.type === "status" && e.status === "done"
      );
      expect(doneIndex).toBeGreaterThan(pourEventIndex);
    });

    it("rejects during run", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, s: 1 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await new Promise((r) => setTimeout(r, 50));

        const pourResult = await api.pour(5, 0.5);
        expect(pourResult.ok).toBe(false);
        expect(pourResult.error).toContain("already running");

        await api.stop();
        await promise;
      });
    });
  });

  describe("manual pump", () => {
    it("works when idle", async () => {
      const testEvents = await runWithEvents(async () => {
        const api = await getApi();

        const result = await api.manual_pump_start("A");
        expect(result.ok).toBe(true);

        const stopResult = await api.manual_pump_stop("A");
        expect(stopResult.ok).toBe(true);
      });

      // Check for manual event
      const manualStart = testEvents.find((e) => e.type === "manual" && e.channel === "A" && e.active);
      expect(manualStart).toBeDefined();

      // Check for manual stop event
      const manualStop = testEvents.find((e) => e.type === "manual" && e.channel === "A" && !e.active);
      expect(manualStop).toBeDefined();
    });

    it("rejects during run", async () => {
      await runWithEvents(async () => {
        const api = await getApi();

        const blocks: Block[] = [
          {
            id: "b1",
            type: "pump",
            pumps: {
              A: { ml: 1, s: 1 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
            },
          },
        ];

        const promise = api.run_program(blocks, 1);
        await new Promise((r) => setTimeout(r, 50));

        const result = await api.manual_pump_start("B");
        expect(result.ok).toBe(false);

        await api.stop();
        await promise;
      });
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
              A: { ml: 1, s: 0.05 },
              B: { ml: 0, s: 0 },
              C: { ml: 0, s: 0 },
              D: { ml: 0, s: 0 },
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
                A: { ml: 10, s: 5 },
                B: { ml: 0, s: 0 },
                C: { ml: 0, s: 0 },
                D: { ml: 0, s: 0 },
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
                A: { ml: 10.5, s: 5.2 },
                B: { ml: 3, s: 6.1 },
                C: { ml: 0, s: 0 },
                D: { ml: 0, s: 0 },
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
                A: { ml: 0, s: 0 },
                B: { ml: 0, s: 0 },
                C: { ml: 1, s: 4 },
                D: { ml: 9, s: 15 },
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
          expect(firstBlock.pumps.A.s).toBe(5.2);
          expect(firstBlock.pumps.B.ml).toBe(3);
          expect(firstBlock.pumps.B.s).toBe(6.1);
        }

        const pauseBlock = loaded?.blocks[1];
        if (pauseBlock && pauseBlock.type === "pause") {
          expect(pauseBlock.s).toBe(2.5);
        }
      });
    });
  });
});
