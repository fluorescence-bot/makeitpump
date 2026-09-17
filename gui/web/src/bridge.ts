import type { BackendApi, BackendEvent, Block, Preset, Channel, RunStatus } from "./types";

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

// Mock backend implementation
class MockBackend implements BackendApi {
  private running = false;
  private paused = false;
  private pausedBlock: { remaining_ml: number; remaining_s: number } | null = null;

  async run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }> {
    if (this.running) {
      return { ok: false, error: "Program already running" };
    }

    this.running = true;
    this.paused = false;
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
            let ml = block.ml;
            let s = block.s;

            if (this.pausedBlock) {
              ml = this.pausedBlock.remaining_ml;
              s = this.pausedBlock.remaining_s;
              this.pausedBlock = null;
            }

            const start = Date.now();
            const duration = s * 1000;

            while (this.running && Date.now() - start < duration) {
              if (this.paused) {
                const elapsed = Date.now() - start;
                this.pausedBlock = {
                  remaining_ml: ml * (1 - elapsed / duration),
                  remaining_s: (duration - elapsed) / 1000,
                };
                emitEvent({ type: "status", status: "paused" });
                while (this.paused && this.running) {
                  await new Promise(r => setTimeout(r, 50));
                }
                if (!this.running) break;
                emitEvent({ type: "status", status: "raining" });
              }
              await new Promise(r => setTimeout(r, 50));
            }
          } else if (block.type === "pause") {
            const start = Date.now();
            const duration = block.s * 1000;

            while (this.running && Date.now() - start < duration) {
              if (this.paused) {
                emitEvent({ type: "status", status: "paused" });
                while (this.paused && this.running) {
                  await new Promise(r => setTimeout(r, 50));
                }
                if (!this.running) break;
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
      this.pausedBlock = null;
    }

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
    this.pausedBlock = null;
    emitEvent({ type: "status", status: "stopped" });
    return { ok: true };
  }

  async pour(ml: number, s: number): Promise<{ ok: boolean; error?: string }> {
    if (this.running) {
      return { ok: false, error: "Program already running" };
    }

    this.running = true;
    this.paused = false;
    emitEvent({ type: "pour_start", ml, s });
    emitEvent({ type: "status", status: "pouring" });

    const start = Date.now();
    const duration = s * 1000;

    try {
      while (this.running && Date.now() - start < duration) {
        await new Promise(r => setTimeout(r, 50));
      }

      if (this.running) {
        emitEvent({ type: "status", status: "done" });
      }
    } finally {
      this.running = false;
    }

    return { ok: true };
  }

  async manual_pump_start(channel: Channel): Promise<{ ok: boolean }> {
    emitEvent({ type: "manual", channel, active: true });
    return { ok: true };
  }

  async manual_pump_stop(channel: Channel): Promise<{ ok: boolean }> {
    emitEvent({ type: "manual", channel, active: false });
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
      input.accept = ".json";
      input.onchange = (e) => {
        const file = (e.target as HTMLInputElement).files?.[0];
        if (!file) {
          resolve(null);
          return;
        }
        const reader = new FileReader();
        reader.onload = (ev) => {
          try {
            const preset = JSON.parse(ev.target?.result as string);
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
      const blob = new Blob([JSON.stringify(preset, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${preset.name}.json`;
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
          { id: "1", type: "pump" as const, channel: "A" as const, ml: 5, s: 10 },
          { id: "2", type: "pause" as const, s: 5 },
          { id: "3", type: "pump" as const, channel: "B" as const, ml: 3, s: 6 },
          { id: "4", type: "pump" as const, channel: "C" as const, ml: 1, s: 4 },
          { id: "5", type: "pump" as const, channel: "D" as const, ml: 9, s: 15 },
        ],
        repeat: 1,
      };
      localStorage.setItem("rainmixer_presets", JSON.stringify(presets));
    }
  } catch {
    // Ignore
  }
}

export async function getApi(): Promise<BackendApi> {
  return new Promise((resolve) => {
    // Try to detect pywebview
    if (typeof (window as any).pywebview !== "undefined" && (window as any).pywebview.api) {
      const timeout = setTimeout(() => {
        mockMode = true;
        seedExamplePreset();
        resolve(mockApi);
      }, 800);

      (window as any).pywebview.api.ready = () => {
        clearTimeout(timeout);
        mockMode = false;

        // Wrap the real API to set up event listening
        const realApi = (window as any).pywebview.api;
        (window as any).__onBackendEvent = (e: BackendEvent) => {
          emitEvent(e);
        };

        resolve(realApi);
      };

      (window as any).pywebview.api.ready();
    } else {
      mockMode = true;
      seedExamplePreset();
      resolve(mockApi);
    }
  });
}
