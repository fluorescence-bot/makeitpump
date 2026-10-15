import { describe, it, expect } from "vitest";
import { sessionCsv, sessionTotals, type LogEntry } from "./sessionlog";

const t = new Date("2026-09-24T10:00:00Z");
const entries: LogEntry[] = [
  { time: t, channel: "A", ml: 1, source: "manual" },
  { time: t, channel: "A", ml: 2.5, source: "program" },
  { time: t, channel: "C", ml: 0.5, source: "manual" },
  { time: t, channel: "A", ml: 1, source: "manual" },
];
const names = { A: "Milk", B: "Water", C: "Na,OH", D: "Final" };

describe("sessionTotals", () => {
  it("sums per channel and per source", () => {
    const totals = sessionTotals(entries);
    expect(totals.A).toEqual({ manual: 2, program: 2.5, total: 4.5 });
    expect(totals.B).toEqual({ manual: 0, program: 0, total: 0 });
    expect(totals.C.total).toBe(0.5);
    expect(totals.D.total).toBe(0);
  });
});

describe("sessionCsv", () => {
  it("lists entries then totals, quoting names with commas", () => {
    const lines = sessionCsv(entries, names).trim().split("\n");
    expect(lines[0]).toBe("time,channel,name,source,ml");
    expect(lines[1]).toBe("2026-09-24T10:00:00.000Z,A,Milk,manual,1.000");
    expect(lines).toContain('total,C,"Na,OH",all,0.500');
    expect(lines).toContain("total,A,Milk,all,4.500");
    expect(lines.length).toBe(1 + entries.length + 4);
  });
});
