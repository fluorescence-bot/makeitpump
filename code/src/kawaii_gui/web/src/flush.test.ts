import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { getApi, onEvent } from "./bridge";
import { CLEAN_ROUNDS, FLUSH_EXCESS_ML, cleanPlan, flow, flushMl, tubeLengthError, tubeVolumeMl } from "./flow";
import type { BackendEvent } from "./types";

const until = async (cond: () => boolean, ms = 6000) => {
  const end = Date.now() + ms;
  while (!cond() && Date.now() < end) await new Promise((r) => setTimeout(r, 20));
  expect(cond()).toBe(true);
};

describe("flush volumes (mirror flush.py)", () => {
  it.each([
    [100, 0.61, 3.61],
    [304, 1.86, 4.86],
    [500, 3.06, 6.06],
    [1000, 6.11, 9.11],
  ])("a %i mm tube holds %f mL and flushes %f mL", (mm, internal, total) => {
    expect(Number(tubeVolumeMl(mm).toFixed(2))).toBe(internal);
    expect(Number(flushMl(mm).toFixed(2))).toBe(total);
  });

  it("tubeLengthError", () => {
    expect(tubeLengthError(304)).toBeNull();
    expect(tubeLengthError(0)).not.toBeNull();
    expect(tubeLengthError(-5)).not.toBeNull();
    expect(tubeLengthError(NaN)).not.toBeNull();
    expect(tubeLengthError(20000)).not.toBeNull();
  });

  it("cleanPlan rinses A-C into the bowl, then drains it through D, each round", () => {
    const f = [100, 200, 300, 400].map(flushMl);
    const plan = cleanPlan([100, 200, 300, 400]);
    expect(plan.steps).toHaveLength(2 * CLEAN_ROUNDS);
    expect(plan.steps[0]).toEqual([f[0], f[1], f[2], 0]);
    expect(plan.steps[1][3]).toBeCloseTo(f[0] + f[1] + f[2] + f[3]);
    expect(plan.waterMl[2]).toBeCloseTo(CLEAN_ROUNDS * f[2]);
  });
});

describe("bridge mock: tube lengths, flush and clean", () => {
  const realFlow = flow.mlPerS;
  const events: BackendEvent[] = [];
  beforeAll(() => {
    flow.mlPerS = 100;   // quick
    onEvent((e) => events.push(e));
  });
  afterAll(() => {
    flow.mlPerS = realFlow;
  });

  it("refuses a flush before the tube length is set", async () => {
    localStorage.removeItem("rainmixer_tube_lengths");
    const api = await getApi();
    expect(await api.flush(["A"])).toEqual({ ok: false, error: "Set the tube length of A first" });
    expect((await api.get_tube_lengths()).mm).toEqual({ A: null, B: null, C: null, D: null });
  });

  it("saves tube lengths and refuses bad ones", async () => {
    const api = await getApi();
    expect(await api.set_tube_lengths({ A: 304, D: 500 })).toEqual({ ok: true, mm: { A: 304, B: null, C: null, D: 500 } });
    const bad = await api.set_tube_lengths({ B: -1 });
    expect(bad.ok).toBe(false);
    expect(bad.mm.B).toBeNull();
    expect((await api.get_tube_lengths()).mm.A).toBe(304);
  });

  it("flushes without counting anything", async () => {
    const api = await getApi();
    await api.set_tube_lengths({ A: 304 });
    events.length = 0;
    expect(await api.flush(["A"])).toEqual({ ok: true });
    expect(await api.flush(["A"])).toEqual({ ok: false, error: "Busy" });
    await until(() => events.some((e) => e.type === "status" && e.status === "ready"));
    expect(events.find((e) => e.type === "flush_start")).toEqual({ type: "flush_start", volumes: { A: flushMl(304) } });
    expect(events.some((e) => e.type === "pumped")).toBe(false);
  });

  it("cleans in rounds, with a message per step", async () => {
    const api = await getApi();
    await api.set_tube_lengths({ A: 10, B: 10, C: 10, D: 10 });
    events.length = 0;
    expect(await api.clean()).toEqual({ ok: true });
    await until(() => events.some((e) => e.type === "status" && e.status === "ready"));
    const messages = events.filter((e) => e.type === "status" && e.status === "cleaning" && e.message).map((e) => (e as { message?: string }).message);
    expect(messages).toHaveLength(2 * CLEAN_ROUNDS);
    expect(messages[0]).toBe(`Round 1/${CLEAN_ROUNDS}: rinsing A-C into the bowl`);
    expect(events.some((e) => e.type === "pumped")).toBe(false);
    expect(FLUSH_EXCESS_ML).toBe(3);
  });
});
