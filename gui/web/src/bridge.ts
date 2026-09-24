import type { BackendApi, BackendEvent, Block, Preset, Channel, ChannelPump, PumpSource, RunStatus } from "./types";
import { pumpSeconds } from "./flow";

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
    const { ml, speed } = pumps[ch]!;
    if (ml < 0) return `${ch}: negative mL`;
    if (!(speed >= 0 && speed <= 100)) return `${ch}: speed must be 0-100`;
    if (ml > 0 && speed === 0) return `${ch}: mL set but speed = 0`;
  }
  return null;
}

// Mock backend implementation
class MockBackend implements BackendApi {
  private running = false;
  private paused = false;
  private pausedAt = 0;
  private activeStatus: RunStatus = "raining";

  async run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }> {
    if (this.running) {
      return { ok: false, error: "Program already running" };
    }
    for (const block of blocks) {
      const error = block.type === "pump" ? pumpsError(block.pumps) : null;
      if (error) return { ok: false, error };
    }

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
          if (!this.running) break;

          const block = blocks[i];
          emitEvent({ type: "block_start", index: i, repeat: r, block });

          if (block.type === "pump") {
            await this.runPumpBlock(block.pumps, "program");
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

  // Mirrors Api._run_pump_block: waits for the slowest channel (not counting paused
  // time), then emits one `pumped` event per channel with the mL actually delivered.
  private async runPumpBlock(pumps: Partial<Record<Channel, ChannelPump>>, source: PumpSource): Promise<void> {
    const jobs = (Object.keys(pumps) as Channel[])
      .map((ch) => ({ ch, ml: pumps[ch]!.ml, s: pumpSeconds(pumps[ch]!.ml, pumps[ch]!.speed) }))
      .filter((j) => j.s > 0);
    if (!jobs.length) return;
    const duration = Math.max(...jobs.map((j) => j.s)) * 1000;
    let start = Date.now();
    while (this.running && Date.now() - start < duration) {
      if (this.paused) {
        this.pausedAt = Date.now();
        emitEvent({ type: "status", status: "paused" });
        while (this.paused && this.running) {
          await new Promise(r => setTimeout(r, 50));
        }
        // Shift the start time to account for pause duration
        start += Date.now() - this.pausedAt;
        if (!this.running) break;
        emitEvent({ type: "status", status: this.activeStatus });
      }
      await new Promise(r => setTimeout(r, 50));
    }
    const elapsed = (Date.now() - start) / 1000;
    for (const j of jobs) {
      const ml = elapsed >= j.s ? j.ml : (j.ml * elapsed) / j.s;
      if (ml > 0) emitEvent({ type: "pumped", channel: j.ch, ml, source });
    }
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

  async manual_dose(channel: Channel, ml: number, speed: number): Promise<{ ok: boolean; error?: string }> {
    if (ml <= 0 || !(speed > 0 && speed <= 100)) {
      return { ok: false, error: "Dose needs mL > 0 and 0 < speed <= 100" };
    }
    if (this.running) {
      return { ok: false, error: "Busy" };
    }

    this.running = true;
    this.paused = false;
    this.activeStatus = "dosing";
    emitEvent({ type: "status", status: "dosing" });
    emitEvent({ type: "dose_start", channel, ml, speed });

    // Like the Python runner, the call returns right away and the dose runs in the background.
    (async () => {
      try {
        await this.runPumpBlock({ [channel]: { ml, speed } }, "manual");
        if (this.running) emitEvent({ type: "status", status: "ready" });
      } finally {
        this.running = false;
        this.paused = false;
      }
    })();

    return { ok: true };
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
              A: { ml: 5, speed: 80 },
              B: { ml: 3, speed: 80 },
              C: { ml: 0, speed: 80 },
              D: { ml: 0, speed: 80 },
            },
          },
          { id: "b2", type: "pause" as const, s: 5 },
          {
            id: "b3",
            type: "pump" as const,
            pumps: {
              A: { ml: 0, speed: 80 },
              B: { ml: 0, speed: 80 },
              C: { ml: 1, speed: 40 },
              D: { ml: 0, speed: 80 },
            },
          },
          {
            id: "b4",
            type: "pump" as const,
            pumps: {
              A: { ml: 0, speed: 80 },
              B: { ml: 0, speed: 80 },
              C: { ml: 0, speed: 80 },
              D: { ml: 9, speed: 80 },
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
