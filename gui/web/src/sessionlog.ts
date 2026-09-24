import type { Channel, PumpSource } from "./types";

export interface LogEntry {
  time: Date;
  channel: Channel;
  ml: number;
  source: PumpSource;
}

export type ChannelTotals = Record<Channel, { manual: number; program: number; total: number }>;

const CHANNELS: Channel[] = ["A", "B", "C", "D"];

/** Sum delivered mL per channel, split by source. */
export function sessionTotals(entries: LogEntry[]): ChannelTotals {
  const totals = {} as ChannelTotals;
  for (const ch of CHANNELS) totals[ch] = { manual: 0, program: 0, total: 0 };
  for (const e of entries) {
    totals[e.channel][e.source] += e.ml;
    totals[e.channel].total += e.ml;
  }
  return totals;
}

/** One row per pumped event, then a per-channel total row. */
export function sessionCsv(entries: LogEntry[], names: Record<Channel, string>): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const rows = ["time,channel,name,source,ml"];
  for (const e of entries) {
    rows.push([e.time.toISOString(), e.channel, esc(names[e.channel]), e.source, e.ml.toFixed(3)].join(","));
  }
  const totals = sessionTotals(entries);
  for (const ch of CHANNELS) {
    rows.push(["total", ch, esc(names[ch]), "all", totals[ch].total.toFixed(3)].join(","));
  }
  return rows.join("\n") + "\n";
}
