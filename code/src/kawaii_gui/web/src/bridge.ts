import type { BackendApi, BackendEvent, Block, Preset, Channel, ChannelPump, PumpInfo, PumpSource, RotatorState, RunStatus, SerialPort, TubeLengths, TubingInfo } from "./types";
import { CLEAN_ROUNDS, cleanPlan, flushMl, pumpSeconds, rotate, rotateError, tubeLengthError } from "./flow";

let listeners: ((e: BackendEvent) => void)[] = [];
let mockMode = false;

export function onEvent(cb: (e: BackendEvent) => void): void {
  listeners.push(cb);
}

function emitEvent(e: BackendEvent): void {
  listeners.forEach(cb => cb(e));
}

export function isMock(): boolean {
  return mockMode;
}

// Simple YAML serializer for preset data (nested objects/arrays of primitives)
function yamlStringify(obj: any, indent = 0): string {
  const spaces = "  ".repeat(indent);
  if (obj === null || obj === undefined) {
    return "null";
  }
  if (typeof obj === "boolean") {
    return obj ? "true" : "false";
  }
  if (typeof obj === "number") {
    return String(obj);
  }
  if (typeof obj === "string") {
    // Quote if contains special chars or newlines
    if (/[:\n\r"']|^\s|\s$|^-|^#/.test(obj) || obj.includes(": ")) {
      return JSON.stringify(obj);
    }
    return obj;
  }
  if (Array.isArray(obj)) {
    if (obj.length === 0) return "[]";
    return obj
      .map((item, i) => {
        const itemStr = yamlStringify(item, indent + 1);
        return i === 0 ? `\n${spaces}  - ${itemStr}` : `${spaces}  - ${itemStr}`;
      })
      .join("\n");
  }
  if (typeof obj === "object") {
    const keys = Object.keys(obj);
    if (keys.length === 0) return "{}";
    return keys
      .map((key) => {
        const val = obj[key];
        const valStr = yamlStringify(val, indent + 1);
        if (valStr.startsWith("\n")) {
          return `${spaces}${key}:${valStr}`;
        }
        return `${spaces}${key}: ${valStr}`;
      })
      .join("\n");
  }
  return String(obj);
}

// Simple YAML parser for preset data
function yamlParse(yaml: string): any {
  const lines = yaml.split("\n").map((line) => line.replace(/\r$/, ""));
  let index = 0;

  function parseValue(line: string, indent: number): any {
    line = line.trim();
    if (!line || line.startsWith("#")) return null;

    // Array item
    if (line.startsWith("- ")) {
      return parseScalar(line.substring(2).trim());
    }

    // Key-value pair
    const match = line.match(/^([^:]+):\s*(.*)/);
    if (match) {
      const key = match[1].trim();
      const val = match[2].trim();
      return { key, val };
    }

    return null;
  }

  function parseScalar(val: string): any {
    val = val.trim();
    if (val === "null") return null;
    if (val === "true") return true;
    if (val === "false") return false;
    if (/^\d+$/.test(val)) return parseInt(val, 10);
    if (/^\d*\.\d+$/.test(val)) return parseFloat(val);
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      return JSON.parse(val);
    }
    return val;
  }

  function getIndent(line: string): number {
    const match = line.match(/^(\s*)/);
    return match ? match[1].length : 0;
  }

  function parseObject(baseIndent: number): any {
    const obj: any = {};
    const arr: any[] = [];
    let isArray = false;

    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim() || line.trim().startsWith("#")) {
        index++;
        continue;
      }

      const indent = getIndent(line);
      if (indent < baseIndent) break;

      if (indent === baseIndent) {
        const trimmed = line.trim();
        if (trimmed.startsWith("- ")) {
          isArray = true;
          const itemStr = trimmed.substring(2).trim();
          const itemVal = parseScalar(itemStr);
          if (itemVal === null && !itemStr.startsWith("{")) {
            index++;
            arr.push(parseObject(baseIndent + 2));
          } else {
            arr.push(itemVal);
            index++;
          }
        } else {
          const match = trimmed.match(/^([^:]+):\s*(.*)/);
          if (match) {
            const key = match[1].trim();
            const val = match[2].trim();
            index++;
            if (!val) {
              obj[key] = parseObject(indent + 2);
            } else {
              obj[key] = parseScalar(val);
            }
          } else {
            index++;
          }
        }
      } else {
        break;
      }
    }

    return isArray ? arr : obj;
  }

  return parseObject(0);
}

// Why a block's pumps are invalid, or null if they are fine (mirrors api.pumps_error)
export function pumpsError(pumps: Partial<Record<Channel, ChannelPump>>): string | null {
  for (const ch of Object.keys(pumps) as Channel[]) {
    const { ml } = pumps[ch]!;
    if (ml < 0) return `${ch}: negative mL`;
  }
  return null;
}

// Why the rotator can't take a move right now, or null if it can (mirrors api.Api._rotator_unavailable)
export function rotatorUnavailable(state: RotatorState): string | null {
  if (state === "disconnected" || state === "error") return "Cuvette rotator not connected";
  if (state === "homing" || state === "moving") return "Cuvette rotator is busy";
  return null;
}

// Why a program can't run, or null if it can (same checks as api.Api.run_program)
export function programError(blocks: Block[], rotator: RotatorState): string | null {
  for (const block of blocks) {
    const error =
      block.type === "pump" ? pumpsError(block.pumps) :
      block.type === "rotate" ? rotateError(block.deg) :
      null;
    if (error) return error;
  }
  if (blocks.some((b) => b.type === "rotate")) return rotatorUnavailable(rotator);
  return null;
}

const CHANNELS: Channel[] = ["A", "B", "C", "D"];

// Tubing inner diameter range the pump accepts (ismatec.util.Tubing ends), any value in it
export const TUBING_MIN_MM = 0.13;
export const TUBING_MAX_MM = 3.17;

// Mock backend implementation
class MockBackend implements BackendApi {
  private running = false;
  private paused = false;
  private pausedAt = 0;
  private activeStatus: RunStatus = "raining";
  // Demo rotator: "connects" and homes in a moment, then moves at rotate.sPerDeg
  private rotatorState: RotatorState = "disconnected";
  private rotatorDeg: number | null = null;
  private tubingMm = 1.52;
  private pumpLogging = false;

  async run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }> {
    if (this.running) {
      return { ok: false, error: "Program already running" };
    }
    const error = programError(blocks, this.rotatorState);
    if (error) return { ok: false, error };

    this.running = true;
    this.paused = false;
    this.pausedAt = 0;
    this.activeStatus = "raining";
    emitEvent({ type: "status", status: "raining" });

    const total_steps = blocks.length * repeat;
    let completed_steps = 0;

    try {
      for (let r = 0; r < repeat; r++) {
        for (let i = 0; i < blocks.length; i++) {
          await this.waitIfPaused();
          if (!this.running) break;

          const block = blocks[i];
          emitEvent({ type: "block_start", index: i, repeat: r, block });

          if (block.type === "pump") {
            await this.runPumpBlock(block.pumps, "program");
          } else if (block.type === "rotate") {
            await this.runRotate(block.deg);
          } else if (block.type === "pause") {
            const duration = block.s * 1000;
            let start = Date.now();

            while (this.running && Date.now() - start < duration) {
              if (this.paused) {
                this.pausedAt = Date.now();
                emitEvent({ type: "status", status: "paused" });
                while (this.paused && this.running) {
                  await new Promise(r => setTimeout(r, 50));
                }
                if (!this.running) break;
                start += Date.now() - this.pausedAt;
                emitEvent({ type: "status", status: "raining" });
              }
              await new Promise(r => setTimeout(r, 50));
            }
          }

          emitEvent({ type: "block_end", index: i, repeat: r });
          completed_steps++;
          emitEvent({ type: "progress", fraction: completed_steps / total_steps });

          if (!this.running) break;
        }
        if (!this.running) break;
      }

      if (this.running) {
        emitEvent({ type: "status", status: "done" });
      }
    } finally {
      this.running = false;
      this.paused = false;
      this.pausedAt = 0;
    }

    return { ok: true };
  }

  // Mirrors Api._wait_if_paused: blocks that can't pause (pump, rotate) leave a
  // pause request for here, between blocks.
  private async waitIfPaused(): Promise<void> {
    if (!this.paused) return;
    emitEvent({ type: "status", status: "paused" });
    while (this.paused && this.running) {
      await new Promise(r => setTimeout(r, 50));
    }
    if (this.running) emitEvent({ type: "status", status: this.activeStatus });
  }

  // Mirrors Api._run_pump_block: waits for the slowest channel and can't be
  // paused, then emits one `pumped` event per channel with the mL delivered.
  private async runPumpBlock(pumps: Partial<Record<Channel, ChannelPump>>, source: PumpSource): Promise<void> {
    const jobs = (Object.keys(pumps) as Channel[])
      .map((ch) => ({ ch, ml: pumps[ch]!.ml, s: pumpSeconds(pumps[ch]!.ml) }))
      .filter((j) => j.s > 0);
    if (!jobs.length || !this.running) return;
    const duration = Math.max(...jobs.map((j) => j.s)) * 1000;
    const start = Date.now();
    while (this.running && Date.now() - start < duration) {
      await new Promise(r => setTimeout(r, 50));
    }
    const elapsed = this.running ? Infinity : (Date.now() - start) / 1000;
    for (const j of jobs) {
      const ml = elapsed >= j.s ? j.ml : (j.ml * elapsed) / j.s;
      if (ml > 0) emitEvent({ type: "pumped", channel: j.ch, ml, source });
    }
  }

  // Mirrors Api._run_rotate: optionally re-home on the 0° switch first; the move
  // can't be paused; Stop stops waiting for it
  private async runRotate(deg: number, homeFirst = false): Promise<void> {
    if (homeFirst) {
      this.rotatorState = "homing";
      emitEvent({ type: "rotator", state: "homing", deg: null });
      // back to the switch, plus the back-off and calibration steps (~30°)
      const homeEnd = Date.now() + ((this.rotatorDeg ?? 0) + 30) * rotate.sPerDeg * 1000;
      this.rotatorDeg = 0;
      while (this.running && Date.now() < homeEnd) await new Promise(r => setTimeout(r, 50));
      if (!this.running) {
        this.rotatorState = "ready";
        emitEvent({ type: "rotator", state: "ready", deg: 0 });
        return;
      }
    }
    const from = this.rotatorDeg ?? 0;
    this.rotatorState = "moving";
    emitEvent({ type: "rotator", state: "moving", deg: from, target: deg });
    const end = Date.now() + Math.abs(deg - from) * rotate.sPerDeg * 1000;
    while (this.running && Date.now() < end) await new Promise(r => setTimeout(r, 50));
    this.rotatorState = "ready";
    this.rotatorDeg = deg;
    emitEvent({ type: "rotator", state: "ready", deg });
  }

  async list_ports(): Promise<SerialPort[]> {
    return [];
  }

  async rotator_connect(_port?: string | null): Promise<{ ok: boolean; error?: string }> {
    if (this.rotatorState === "homing") return { ok: true };
    this.rotatorState = "homing";
    emitEvent({ type: "rotator", state: "homing", deg: null });
    setTimeout(() => {
      this.rotatorState = "ready";
      this.rotatorDeg = 0;
      emitEvent({ type: "rotator", state: "ready", deg: 0 });
    }, 1500);
    return { ok: true };
  }

  async manual_rotate(deg: number): Promise<{ ok: boolean; error?: string }> {
    const error = rotateError(deg) ?? rotatorUnavailable(this.rotatorState);
    if (error) return { ok: false, error };
    if (this.running) return { ok: false, error: "Busy" };

    this.running = true;
    this.paused = false;
    this.activeStatus = "rotating";
    emitEvent({ type: "status", status: "rotating" });
    (async () => {
      try {
        await this.runRotate(deg, true);
        if (this.running) emitEvent({ type: "status", status: "ready" });
      } finally {
        this.running = false;
        this.paused = false;
      }
    })();
    return { ok: true };
  }

  async pause_run(): Promise<{ ok: boolean }> {
    if (!this.running || this.paused) {
      return { ok: false };
    }
    this.paused = true;
    return { ok: true };
  }

  async resume_run(): Promise<{ ok: boolean }> {
    if (!this.running || !this.paused) {
      return { ok: false };
    }
    this.paused = false;
    return { ok: true };
  }

  async stop(): Promise<{ ok: boolean }> {
    this.running = false;
    this.paused = false;
    this.pausedAt = 0;
    emitEvent({ type: "status", status: "stopped" });
    return { ok: true };
  }

  async manual_dose(channel: Channel, ml: number): Promise<{ ok: boolean; error?: string }> {
    if (!(ml > 0)) {
      return { ok: false, error: "Dose needs mL > 0" };
    }
    if (this.running) {
      return { ok: false, error: "Busy" };
    }

    this.running = true;
    this.paused = false;
    this.activeStatus = "dosing";
    emitEvent({ type: "status", status: "dosing" });
    emitEvent({ type: "dose_start", channel, ml });

    // Like the Python runner, the call returns right away and the dose runs in the background.
    (async () => {
      try {
        await this.runPumpBlock({ [channel]: { ml } }, "manual");
        if (this.running) emitEvent({ type: "status", status: "ready" });
      } finally {
        this.running = false;
        this.paused = false;
      }
    })();

    return { ok: true };
  }

  // -- Tube lengths, flushing and cleaning (mirror api.py) -------------------

  private tubeLengths(): Record<Channel, number | null> {
    const lengths: Record<Channel, number | null> = { A: null, B: null, C: null, D: null };
    try {
      const saved = JSON.parse(localStorage.getItem("rainmixer_tube_lengths") || "{}");
      for (const ch of CHANNELS) {
        const v = saved[ch];
        if (typeof v === "number" && tubeLengthError(v) === null) lengths[ch] = v;
      }
    } catch {
      // nothing saved
    }
    return lengths;
  }

  async get_tube_lengths(): Promise<TubeLengths> {
    return { ok: true, mm: this.tubeLengths() };
  }

  async set_tube_lengths(mm: Partial<Record<Channel, number | null>>): Promise<TubeLengths> {
    const lengths = this.tubeLengths();
    for (const [ch, value] of Object.entries(mm) as [Channel, number | null][]) {
      if (!CHANNELS.includes(ch)) return { ok: false, error: `Unknown channel ${ch}`, mm: lengths };
      if (value === null) {
        lengths[ch] = null;
        continue;
      }
      const error = tubeLengthError(value);
      if (error) return { ok: false, error: `${ch}: ${error}`, mm: lengths };
      lengths[ch] = Math.round(value * 10) / 10;
    }
    try {
      localStorage.setItem("rainmixer_tube_lengths", JSON.stringify(lengths));
    } catch (e) {
      return { ok: false, error: `Couldn't save the settings: ${e}`, mm: this.tubeLengths() };
    }
    return { ok: true, mm: lengths };
  }

  // Mirrors Api._start_service: refused while busy, status `status`, then ready
  private startService(status: RunStatus, work: () => Promise<void>): { ok: boolean; error?: string } {
    if (this.running) return { ok: false, error: "Busy" };
    this.running = true;
    this.paused = false;
    this.activeStatus = status;
    emitEvent({ type: "status", status });
    (async () => {
      try {
        await work();
        if (this.running) emitEvent({ type: "status", status: "ready" });
      } finally {
        this.running = false;
        this.paused = false;
      }
    })();
    return { ok: true };
  }

  // Waits as long as the slowest channel of a pump step takes; Stop ends it early
  private async waitPumpStep(volumes: number[]): Promise<void> {
    const end = Date.now() + Math.max(0, ...volumes.map(pumpSeconds)) * 1000;
    while (this.running && Date.now() < end) await new Promise(r => setTimeout(r, 50));
  }

  async flush(channels: Channel[]): Promise<{ ok: boolean; error?: string }> {
    const chs = [...new Set(channels)];
    if (!chs.length) return { ok: false, error: "No channels to flush" };
    if (chs.some((ch) => !CHANNELS.includes(ch))) return { ok: false, error: `Unknown channel in ${chs}` };
    const lengths = this.tubeLengths();
    const missing = chs.filter((ch) => lengths[ch] === null);
    if (missing.length) return { ok: false, error: `Set the tube length of ${missing.join(", ")} first` };
    const volumes: Partial<Record<Channel, number>> = {};
    for (const ch of chs) volumes[ch] = flushMl(lengths[ch]!);
    return this.startService("flushing", async () => {
      emitEvent({ type: "flush_start", volumes });
      await this.waitPumpStep(Object.values(volumes));
    });
  }

  async clean(): Promise<{ ok: boolean; error?: string }> {
    const lengths = this.tubeLengths();
    const missing = CHANNELS.filter((ch) => lengths[ch] === null);
    if (missing.length) return { ok: false, error: `Set the tube length of ${missing.join(", ")} first` };
    const plan = cleanPlan(CHANNELS.map((ch) => lengths[ch]!) as [number, number, number, number]);
    return this.startService("cleaning", async () => {
      for (let i = 0; i < plan.steps.length; i++) {
        if (!this.running) return;
        const what = i % 2 === 0 ? "rinsing A-C into the bowl" : "draining the bowl through D";
        emitEvent({ type: "status", status: "cleaning", message: `Round ${Math.floor(i / 2) + 1}/${CLEAN_ROUNDS}: ${what}` });
        await this.waitPumpStep(plan.steps[i]);
      }
    });
  }

  private tubingInfo(): TubingInfo {
    // Demo max flow: grows with the tube's cross-section, roughly like the real pump
    const maxFlow = Math.round(15 * this.tubingMm ** 2 * 100) / 100;
    const max_ml_per_min = { A: maxFlow, B: maxFlow, C: maxFlow, D: maxFlow };
    return { ok: true, mm: this.tubingMm, min_mm: TUBING_MIN_MM, max_mm: TUBING_MAX_MM, max_ml_per_min };
  }

  private pumpInfo(): PumpInfo {
    return { ok: true, mode: "mock", address: null, message: "Demo mode - mock pump", logging: this.pumpLogging };
  }

  async pump_connect(_port?: string | null): Promise<PumpInfo> {
    if (this.running) return { ...this.pumpInfo(), ok: false, error: "Busy" };
    return this.pumpInfo();
  }

  async set_pump_logging(on: boolean): Promise<PumpInfo> {
    this.pumpLogging = on;
    return this.pumpInfo();
  }

  async get_tubing(): Promise<TubingInfo> {
    return this.tubingInfo();
  }

  async set_tubing(mm: number): Promise<TubingInfo> {
    let error: string | undefined;
    const rounded = Math.round(mm * 100) / 100;
    if (typeof mm !== "number" || !Number.isFinite(mm) || rounded < TUBING_MIN_MM || rounded > TUBING_MAX_MM) {
      error = `Tubing must be ${TUBING_MIN_MM}-${TUBING_MAX_MM} mm`;
    } else if (this.running) error = "Busy";
    else this.tubingMm = rounded;
    return error ? { ...this.tubingInfo(), ok: false, error } : this.tubingInfo();
  }

  async list_presets(): Promise<string[]> {
    try {
      const data = localStorage.getItem("rainmixer_presets");
      if (!data) return [];
      const presets = JSON.parse(data);
      return Object.keys(presets);
    } catch {
      return [];
    }
  }

  async load_preset(name: string): Promise<Preset | null> {
    try {
      const data = localStorage.getItem("rainmixer_presets");
      if (!data) return null;
      const presets = JSON.parse(data);
      return presets[name] || null;
    } catch {
      return null;
    }
  }

  async save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }> {
    try {
      const data = localStorage.getItem("rainmixer_presets") || "{}";
      const presets = JSON.parse(data);
      presets[preset.name] = preset;
      localStorage.setItem("rainmixer_presets", JSON.stringify(presets));
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  async import_preset_file(): Promise<Preset | null> {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".yaml,.yml";
      input.onchange = (e) => {
        const file = (e.target as HTMLInputElement).files?.[0];
        if (!file) {
          resolve(null);
          return;
        }
        const reader = new FileReader();
        reader.onload = (ev) => {
          try {
            const preset = yamlParse(ev.target?.result as string);
            resolve(preset);
          } catch {
            resolve(null);
          }
        };
        reader.readAsText(file);
      };
      input.click();
    });
  }

  async export_preset_file(preset: Preset): Promise<{ ok: boolean }> {
    try {
      const yaml = yamlStringify(preset);
      const blob = new Blob([yaml], {
        type: "text/yaml",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${preset.name}.yaml`;
      a.click();
      URL.revokeObjectURL(url);
      return { ok: true };
    } catch {
      return { ok: false };
    }
  }
}

const mockApi = new MockBackend();

// Seed with Example preset
function seedExamplePreset(): void {
  try {
    const data = localStorage.getItem("rainmixer_presets");
    const presets = data ? JSON.parse(data) : {};
    if (!presets["Example"]) {
      presets["Example"] = {
        name: "Example",
        solutions: { A: "Milk", B: "Water", C: "NaOH", D: "Final mix" },
        blocks: [
          {
            id: "b1",
            type: "pump" as const,
            pumps: {
              A: { ml: 5 },
              B: { ml: 3 },
              C: { ml: 0 },
              D: { ml: 0 },
            },
          },
          { id: "b2", type: "pause" as const, s: 5 },
          {
            id: "b3",
            type: "pump" as const,
            pumps: {
              A: { ml: 0 },
              B: { ml: 0 },
              C: { ml: 1 },
              D: { ml: 0 },
            },
          },
          {
            id: "b4",
            type: "pump" as const,
            pumps: {
              A: { ml: 0 },
              B: { ml: 0 },
              C: { ml: 0 },
              D: { ml: 9 },
            },
          },
        ],
        repeat: 1,
      };
      localStorage.setItem("rainmixer_presets", JSON.stringify(presets));
    }
  } catch {
    // Ignore
  }
}

// Python pushes events by calling window.__onBackendEvent(...). Install it right away
// so no event is lost, whether we end up on the real backend or the mock.
(window as any).__onBackendEvent = (e: BackendEvent) => emitEvent(e);

export async function getApi(): Promise<BackendApi> {
  const w = window as any;
  const hasPywebview = () => !!(w.pywebview && w.pywebview.api && typeof w.pywebview.api.run_program === "function");
  if (!hasPywebview()) {
    // pywebview injects its API shortly after page load and fires 'pywebviewready'
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 3000);
      window.addEventListener("pywebviewready", () => { clearTimeout(timer); resolve(); }, { once: true });
    });
  }
  if (hasPywebview()) {
    mockMode = false;
    return w.pywebview.api as BackendApi;
  }
  mockMode = true;
  seedExamplePreset();
  return mockApi;
}
