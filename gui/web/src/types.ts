export type Channel = "A" | "B" | "C" | "D";
export type SourceChannel = "A" | "B" | "C";

// A pump block runs all four channels at the same time. Each channel pumps its own
// mL at its own speed, 0-100 % of the pump's max flow (ml = 0 or speed = 0 means that
// channel is idle). The block lasts as long as its slowest channel.
export interface ChannelPump { ml: number; speed: number; }
export interface PumpBlock { id: string; type: "pump"; pumps: Record<Channel, ChannelPump>; }
export interface PauseBlock { id: string; type: "pause"; s: number; }
// Turns the cuvette robot to an absolute angle: whole degrees, 0-60.
export interface RotateBlock { id: string; type: "rotate"; deg: number; }
export type Block = PumpBlock | PauseBlock | RotateBlock;

export interface Preset {
  name: string;
  solutions: Record<Channel, string>;   // free-text names, e.g. {A:"Milk",B:"Water",C:"NaOH",D:"Final mix"}
  blocks: Block[];
  repeat: number;                        // >= 1
}

export type RunStatus = "ready" | "raining" | "paused" | "dosing" | "rotating" | "done" | "stopped" | "error";
export type RotatorState = "disconnected" | "homing" | "ready" | "moving" | "error";
export type PumpSource = "program" | "manual";

// Events pushed from backend -> UI
export type BackendEvent =
  | { type: "status"; status: RunStatus; message?: string }
  | { type: "block_start"; index: number; repeat: number; block: Block }   // repeat is 0-based
  | { type: "block_end"; index: number; repeat: number }
  | { type: "progress"; fraction: number }                                  // 0..1 of whole run
  | { type: "dose_start"; channel: Channel; ml: number; speed: number }     // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource }     // mL actually delivered (pro-rata if stopped)
  | { type: "rotator"; state: RotatorState; deg: number | null; target?: number; message?: string }; // deg = current angle, null if unknown

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number, speed: number): Promise<{ ok: boolean; error?: string }>;
  rotator_connect(): Promise<{ ok: boolean; error?: string }>;              // find the board, open it, HOME (in the background)
  manual_rotate(deg: number): Promise<{ ok: boolean; error?: string }>;     // one manual rotate, like manual_dose
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.json
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
