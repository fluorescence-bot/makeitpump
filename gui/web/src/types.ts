export type Channel = "A" | "B" | "C" | "D";
export type SourceChannel = "A" | "B" | "C";

// A pump block runs all four channels at the same time. Each channel pumps its own
// mL over its own seconds (s = 0 means that channel is idle). The block lasts max(s).
export interface ChannelPump { ml: number; s: number; }
export interface PumpBlock { id: string; type: "pump"; pumps: Record<Channel, ChannelPump>; }
export interface PauseBlock { id: string; type: "pause"; s: number; }
export type Block = PumpBlock | PauseBlock;

export interface Preset {
  name: string;
  solutions: Record<Channel, string>;   // free-text names, e.g. {A:"Milk",B:"Water",C:"NaOH",D:"Final mix"}
  blocks: Block[];
  repeat: number;                        // >= 1
}

export type RunStatus = "ready" | "raining" | "paused" | "dosing" | "done" | "stopped" | "error";
export type PumpSource = "program" | "manual";

// Events pushed from backend -> UI
export type BackendEvent =
  | { type: "status"; status: RunStatus; message?: string }
  | { type: "block_start"; index: number; repeat: number; block: Block }   // repeat is 0-based
  | { type: "block_end"; index: number; repeat: number }
  | { type: "progress"; fraction: number }                                  // 0..1 of whole run
  | { type: "dose_start"; channel: Channel; ml: number; s: number }         // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource };    // mL actually delivered (pro-rata if stopped)

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number, s: number): Promise<{ ok: boolean; error?: string }>;
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.json
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
