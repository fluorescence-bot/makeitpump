export type Channel = "A" | "B" | "C" | "D";
export type SourceChannel = "A" | "B" | "C";

// A pump block runs all four channels at the same time. Each channel pumps its own
// mL (ml = 0 means that channel is idle) at the pump's own rate. The block lasts
// until the pump reports every channel done.
export interface ChannelPump { ml: number; }
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

// Which pump the backend drives. "real" = the Ismatec pump found at `address`;
// "mock" = no pump was found, so the ismatec library's mock pump stands in.
export type PumpMode = "real" | "mock";
export interface PumpInfo {
  ok: boolean;
  error?: string;
  mode: PumpMode;
  address: string | null;     // serial port of the real pump, null for the mock
  message: string;            // for the UI, e.g. "No pump found on /dev/ttyACM0 - using the mock pump"
  logging: boolean;           // ismatec debug logging on/off
}

// A USB serial port, for the port dropdowns. device: "COM4", "/dev/ttyACM0", "/dev/cu.usbmodem1101".
export interface SerialPort {
  device: string;
  description: string;        // what the OS calls it, e.g. "USB-SERIAL CH340"
}

// Tubing inner diameter set on the pump. It sets the pump's max flow (and so how
// fast a dose runs), so it must match the tube that is fitted.
export interface TubingInfo {
  ok: boolean;
  error?: string;
  mm: number | null;                                   // same on every channel, else null (also when unknown)
  min_mm: number;                                      // range the pump accepts; any value in it,
  max_mm: number;                                      //   rounded to 0.01 mm
  max_ml_per_min: Partial<Record<Channel, number>>;    // calibrated max flow per channel; empty when unknown
}

// Events pushed from backend -> UI
export type BackendEvent =
  | { type: "status"; status: RunStatus; message?: string }
  | { type: "block_start"; index: number; repeat: number; block: Block }   // repeat is 0-based
  | { type: "block_end"; index: number; repeat: number }
  | { type: "progress"; fraction: number }                                  // 0..1 of whole run
  | { type: "dose_start"; channel: Channel; ml: number }                    // a manual click started
  | { type: "pumped"; channel: Channel; ml: number; source: PumpSource }     // mL actually delivered (pro-rata if stopped)
  | { type: "rotator"; state: RotatorState; deg: number | null; target?: number; message?: string }; // deg = current angle, null if unknown

export interface BackendApi {
  run_program(blocks: Block[], repeat: number): Promise<{ ok: boolean; error?: string }>;
  pause_run(): Promise<{ ok: boolean }>;
  resume_run(): Promise<{ ok: boolean }>;
  stop(): Promise<{ ok: boolean }>;
  manual_dose(channel: Channel, ml: number): Promise<{ ok: boolean; error?: string }>;
  list_ports(): Promise<SerialPort[]>;                                       // USB serial ports, sorted by device
  rotator_connect(port?: string | null): Promise<{ ok: boolean; error?: string }>; // open the board on port (none: find it), HOME (in the background)
  manual_rotate(deg: number): Promise<{ ok: boolean; error?: string }>;     // one manual rotate, like manual_dose
  pump_connect(port?: string | null): Promise<PumpInfo>;                    // (re)detect on port (none: every port): real pump, else the mock; refused while busy
  set_pump_logging(on: boolean): Promise<PumpInfo>;                          // ismatec debug logging
  get_tubing(): Promise<TubingInfo>;
  set_tubing(mm: number): Promise<TubingInfo>;                              // every channel; refused while busy
  list_presets(): Promise<string[]>;
  load_preset(name: string): Promise<Preset | null>;
  save_preset(preset: Preset): Promise<{ ok: boolean; error?: string }>;   // saved as presets/<name>.yaml
  import_preset_file(): Promise<Preset | null>;                             // native open dialog
  export_preset_file(preset: Preset): Promise<{ ok: boolean }>;             // native save dialog
}
