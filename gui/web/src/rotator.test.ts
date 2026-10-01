import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { getApi, onEvent, programError, rotatorUnavailable } from "./bridge";
import { rotate, rotateError } from "./flow";
import { computeTotals } from "./totals";
import type { BackendEvent, Block } from "./types";

const until = async (cond: () => boolean, ms = 4000) => {
  const end = Date.now() + ms;
  while (!cond() && Date.now() < end) await new Promise((r) => setTimeout(r, 20));
  expect(cond()).toBe(true);
};

describe("rotator validation", () => {
  it("rotateError mirrors rotator.rotate_error", () => {
    expect(rotateError(0)).toBeNull();
    expect(rotateError(60)).toBeNull();
    expect(rotateError(61)).not.toBeNull();
    expect(rotateError(-1)).not.toBeNull();
    expect(rotateError(30.5)).not.toBeNull();
  });

  it("programs with rotate blocks need a ready rotator", () => {
    const blocks: Block[] = [{ id: "r", type: "rotate", deg: 30 }];
    expect(programError(blocks, "disconnected")).toBe("Cuvette rotator not connected");
    expect(programError(blocks, "homing")).toBe("Cuvette rotator is busy");
    expect(programError(blocks, "ready")).toBeNull();
    expect(programError([{ id: "p", type: "pause", s: 1 }], "disconnected")).toBeNull();
    expect(rotatorUnavailable("moving")).toBe("Cuvette rotator is busy");
  });

  it("block time counts the angle change from 0°", () => {
    const blocks: Block[] = [
      { id: "a", type: "rotate", deg: 30 },
      { id: "b", type: "rotate", deg: 10 },
    ];
    expect(computeTotals(blocks, 1).totalTime).toBeCloseTo(50 * rotate.sPerDeg);
  });
});

describe("bridge mock rotator", () => {
  const real = rotate.sPerDeg;
  beforeAll(() => { rotate.sPerDeg = 0.002; });
  afterAll(() => { rotate.sPerDeg = real; });

  it("connects, homes, then rotates on a manual click and in a program", async () => {
    const events: BackendEvent[] = [];
    onEvent((e) => events.push(e));
    const api = await getApi();

    expect((await api.manual_rotate(20)).ok).toBe(false); // not connected yet
    await api.rotator_connect();
    await until(() => events.some((e) => e.type === "rotator" && e.state === "ready"));

    expect(await api.manual_rotate(20)).toEqual({ ok: true });
    await until(() => events.some((e) => e.type === "status" && e.status === "ready"));
    const statuses = events.filter((e) => e.type === "status").map((e) => (e as any).status);
    expect(statuses).toEqual(["rotating", "ready"]);
    const rotatorEvents = events.filter((e) => e.type === "rotator");
    expect(rotatorEvents[rotatorEvents.length - 1]).toEqual({ type: "rotator", state: "ready", deg: 20 });

    events.length = 0;
    expect(await api.run_program([{ id: "r", type: "rotate", deg: 40 }], 1)).toEqual({ ok: true });
    expect(events.find((e) => e.type === "rotator" && e.state === "moving")).toEqual({
      type: "rotator", state: "moving", deg: 20, target: 40,
    });
    expect(events[events.length - 1]).toEqual({ type: "status", status: "done" });
  });
});
