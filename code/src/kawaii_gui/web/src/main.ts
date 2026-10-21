import "@fontsource/m-plus-rounded-1c/400.css";
import "@fontsource/m-plus-rounded-1c/700.css";
import "@fontsource/m-plus-rounded-1c/800.css";
// Bundled emoji font: Linux often has none (emoji rendered as blank space).
import "@fontsource/noto-color-emoji/400.css";
import "./styles.css";

import { getApi, onEvent, isMock, programError } from "./bridge";
import { cleanPlan, flushMl, pumpSeconds, tubeLengthError, FLUSH_EXCESS_ML, TUBE_INNER_DIAMETER_MM } from "./flow";
import { createBlockEditor } from "./blocks";
import { createScene } from "./scene";
import { computeTotals } from "./totals";
import { sessionCsv, sessionTotals, type LogEntry } from "./sessionlog";
import type { BackendApi, BackendEvent, Preset, Channel, PumpInfo, RotatorState, RunStatus, SerialPort, TubingInfo } from "./types";

const CHANNELS: Channel[] = ["A", "B", "C", "D"];
// Statuses while something runs on the backend's runner (or is paused)
const BUSY_STATUSES: RunStatus[] = ["raining", "dosing", "rotating", "flushing", "cleaning", "paused"];

// Global state
let api: BackendApi;
let currentPreset: Preset = {
  name: "Untitled",
  solutions: { A: "Liquid A", B: "Liquid B", C: "Liquid C", D: "Result" },
  blocks: [],
  repeat: 1,
};

let isRunning = false;
let currentStatus: RunStatus = "ready";
let rotatorState: RotatorState = "disconnected";
// Tube length per channel (mm), for flush volumes; null = not set yet
let tubeLengths: Record<Channel, number | null> = { A: null, B: null, C: null, D: null };
// Shown once the running flush/clean ends: what ran, and what to do next
let afterService: { what: string; next: string } | null = null;
// Everything pumped this session (manual clicks and program blocks), from `pumped` events
const sessionLog: LogEntry[] = [];

// Initialize app
async function init(): Promise<void> {
  // Create HTML structure
  const app = document.getElementById("app")!;
  app.innerHTML = "";

  // Header
  const header = document.createElement("div");
  header.className = "header";
  header.innerHTML = `
    <div class="header-logo">☁️ RainMixer</div>
    <div class="header-controls">
      <select id="preset-select" class="preset-select"></select>
      <button id="btn-save" class="btn">💾 Save</button>
      <button id="btn-load" class="btn">📂 Load</button>
      <button id="btn-export" class="btn">📤 Export</button>
      <button id="btn-new" class="btn">✨ New</button>
    </div>
  `;
  app.appendChild(header);

  // Main area
  const main = document.createElement("div");
  main.className = "main";

  const sceneContainer = document.createElement("div");
  sceneContainer.id = "scene";
  main.appendChild(sceneContainer);

  const programPanel = document.createElement("div");
  programPanel.className = "program-panel";

  const palette = document.createElement("div");
  palette.id = "palette";
  programPanel.appendChild(palette);

  const blocksList = document.createElement("div");
  blocksList.className = "blocks-list";
  blocksList.id = "blocks-list";
  programPanel.appendChild(blocksList);

  const programInfo = document.createElement("div");
  programInfo.className = "program-info";
  programInfo.id = "program-info";
  programPanel.appendChild(programInfo);

  const repeatControl = document.createElement("div");
  repeatControl.className = "repeat-control";
  repeatControl.innerHTML = `
    <label>Repeat ×</label>
    <input id="repeat-input" type="number" min="1" max="99" value="1">
    <label for="tube-input" title="Inner diameter of the pump tubing. The pump doses at 90 % of the max flow it gives, so it must match the fitted tube.">Tube ⌀</label>
    <input id="tube-input" type="number" step="0.01" min="0.13" max="3.17" placeholder="?"><span class="unit">mm</span>
    <span id="tube-flow" class="tube-flow"></span>
    <span class="pump-buttons">
      <select id="pump-port" class="port-select" title="Pump serial port. Auto: try every USB serial port that isn't a known board"></select>
      <button id="btn-pump-redetect" class="btn btn-small" title="Look for the pump again on the chosen port (e.g. after plugging it in)">🔌 Redetect pump</button>
      <button id="btn-pump-log" class="btn btn-small btn-toggle" aria-pressed="false" title="Print the pump library's debug log to the terminal">🐞 Log</button>
    </span>
  `;
  programPanel.appendChild(repeatControl);

  // Tubes: an empty tube must fill before liquid comes out, so tubes are flushed
  // before use (never counted in the totals), and can be rinsed with water
  const flushControl = document.createElement("div");
  flushControl.className = "flush-control";
  flushControl.innerHTML = `
    <label title="Fill the tubes before mixing: each pumps its own volume plus ${FLUSH_EXCESS_ML} mL. Not counted in the totals.">Tubes</label>
    <button id="btn-tube-lengths" class="btn btn-small" title="Length of each channel's tube, for the flush volumes">📏 Lengths</button>
    <button id="btn-flush-in" class="btn btn-small" title="Fill the tubes of the liquids this program uses (A-C), into a waste cup">🚿 Flush in</button>
    <button id="btn-flush-out" class="btn btn-small" title="Fill tube D with the finished mixture, through the cuvette">🚿 Flush out</button>
    <button id="btn-clean" class="btn btn-small" title="Rinse every tube with water from the beakers">🧼 Clean</button>
    <span id="flush-info" class="tube-flow"></span>
  `;
  programPanel.appendChild(flushControl);

  const sessionPanel = document.createElement("div");
  sessionPanel.className = "session-log";
  sessionPanel.innerHTML = `
    <div class="session-log-header">
      <span class="session-log-title">Session log</span>
      <button id="btn-log-copy" class="btn btn-small" title="Copy the log as CSV">📋 Copy CSV</button>
      <button id="btn-log-reset" class="btn btn-small">↺ Reset</button>
    </div>
    <ol id="session-entries" class="session-entries"></ol>
  `;
  programPanel.appendChild(sessionPanel);

  main.appendChild(programPanel);
  app.appendChild(main);

  // Bottom control bar
  const controlBar = document.createElement("div");
  controlBar.className = "control-bar";
  controlBar.innerHTML = `
    <div class="control-group">
      <button id="btn-rain" class="big-btn rain">🌧️ Make it RAIN!</button>
      <button id="btn-pause" class="big-btn">⏸ Pause</button>
      <button id="btn-stop" class="big-btn stop">■ Stop</button>
    </div>
    <div class="control-group">
      <div id="status-pill" class="status-pill status-ready">Ready</div>
      <div class="progress-bar">
        <div id="progress-fill" class="progress-bar-fill"></div>
      </div>
    </div>
  `;

  // Which pump is driven: loud badge for the mock pump (this also covers browser demo mode), subtle dot for a real one
  const pumpBadge = document.createElement("div");
  pumpBadge.id = "pump-badge";
  pumpBadge.hidden = true;
  controlBar.appendChild(pumpBadge);

  app.appendChild(controlBar);

  // Initialize backend
  api = await getApi();

  // Initialize scene
  const scene = createScene(sceneContainer);

  // Initialize block editor
  const blocksEditor = createBlockEditor(blocksList, palette, {
    getNames: () => currentPreset.solutions,
    onChange: (blocks) => {
      currentPreset.blocks = blocks;
      updateProgramInfo();
    },
  });

  // Initialize preset dropdown
  const presetSelect = document.getElementById("preset-select") as HTMLSelectElement;
  async function refreshPresetList(): Promise<void> {
    const names = await api.list_presets();
    presetSelect.innerHTML = "";
    names.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      presetSelect.appendChild(opt);
    });

    // Load Example preset if available
    if (names.includes("Example")) {
      const example = await api.load_preset("Example");
      if (example) {
        currentPreset = example;
        presetSelect.value = "Example";
        updateUI();
      }
    }
  }

  await refreshPresetList();

  // Update program info display
  function updateProgramInfo(): void {
    const info = document.getElementById("program-info")!;
    const blocks = blocksEditor.getBlocks();
    const totals = computeTotals(blocks, currentPreset.repeat);

    info.textContent = `In: ${totals.totalIn.toFixed(1)} mL · Out: ${totals.totalOut.toFixed(1)} mL · Time: ${totals.timeStr}`;
  }

  // Update UI from preset
  function updateUI(): void {
    blocksEditor.setBlocks(currentPreset.blocks);
    // After setBlocks normalizes, update the stored blocks
    currentPreset.blocks = blocksEditor.getBlocks();
    scene.setNames(currentPreset.solutions);
    const repeatInput = document.getElementById("repeat-input") as HTMLInputElement;
    repeatInput.value = String(currentPreset.repeat);
    updateProgramInfo();
    renderSessionLog();
  }

  function renderSessionLog(): void {
    const names = currentPreset.solutions;
    const totals = sessionTotals(sessionLog);
    scene.setTotals({ A: totals.A.total, B: totals.B.total, C: totals.C.total, D: totals.D.total });
    const list = document.getElementById("session-entries")!;
    list.innerHTML = "";
    for (const e of [...sessionLog].reverse()) {
      const li = document.createElement("li");
      li.textContent = `${e.time.toLocaleTimeString()}  ${e.channel} ${names[e.channel]}  +${e.ml.toFixed(2)} mL  (${e.source})`;
      list.appendChild(li);
    }
  }

  document.getElementById("btn-log-reset")!.onclick = () => {
    if (sessionLog.length && !confirm("Clear the session log?")) return;
    sessionLog.length = 0;
    renderSessionLog();
  };

  document.getElementById("btn-log-copy")!.onclick = async () => {
    const btn = document.getElementById("btn-log-copy")!;
    try {
      await navigator.clipboard.writeText(sessionCsv(sessionLog, currentPreset.solutions));
      btn.textContent = "✓ Copied";
    } catch {
      btn.textContent = "Copy failed";
    }
    setTimeout(() => (btn.textContent = "📋 Copy CSV"), 1500);
  };

  // Header buttons
  document.getElementById("preset-select")!.onchange = async (e) => {
    const name = (e.target as HTMLSelectElement).value;
    const preset = await api.load_preset(name);
    if (preset) {
      currentPreset = preset;
      updateUI();
    }
  };

  document.getElementById("btn-new")!.onclick = () => {
    currentPreset = {
      name: "Untitled",
      solutions: { A: "Liquid A", B: "Liquid B", C: "Liquid C", D: "Result" },
      blocks: [],
      repeat: 1,
    };
    presetSelect.value = "";
    updateUI();
  };

  document.getElementById("btn-save")!.onclick = () => {
    showSaveDialog();
  };

  document.getElementById("btn-load")!.onclick = () => {
    presetSelect.click();
  };

  document.getElementById("btn-export")!.onclick = async () => {
    await api.export_preset_file(currentPreset);
  };

  // Save dialog
  function showSaveDialog(): void {
    const overlay = document.createElement("div");
    overlay.className = "save-dialog-overlay";
    document.body.appendChild(overlay);

    const dialog = document.createElement("div");
    dialog.className = "save-dialog";
    dialog.innerHTML = `
      <h3>Save Preset</h3>
      <input id="preset-name-input" type="text" placeholder="Preset name">
      <div class="save-dialog-buttons">
        <button class="btn-cancel">Cancel</button>
        <button class="btn-save">Save</button>
      </div>
    `;
    document.body.appendChild(dialog);

    const nameInput = dialog.querySelector("#preset-name-input") as HTMLInputElement;
    nameInput.value = currentPreset.name;
    nameInput.focus();
    nameInput.select();

    const cancelBtn = dialog.querySelector(".btn-cancel") as HTMLButtonElement;
    const saveBtn = dialog.querySelector(".btn-save") as HTMLButtonElement;

    const close = () => {
      overlay.remove();
      dialog.remove();
    };

    cancelBtn.onclick = close;
    saveBtn.onclick = async () => {
      const name = nameInput.value.trim();
      if (name) {
        currentPreset.name = name;
        await api.save_preset(currentPreset);
        await refreshPresetList();
        presetSelect.value = name;
        close();
      }
    };

    nameInput.onkeydown = (e) => {
      if (e.key === "Enter") saveBtn.click();
      if (e.key === "Escape") close();
    };
  }

  // Scene name changes
  scene.onNameChange((ch: Channel, name: string) => {
    currentPreset.solutions[ch] = name;
    blocksEditor.refreshNames();
    renderSessionLog();
  });

  function showStatusError(message: string): void {
    const statusPill = document.getElementById("status-pill")!;
    statusPill.textContent = message;
    statusPill.className = "status-pill status-error";
    statusPill.title = message;
  }

  // Manual dosing: each click pumps that channel's mL-per-click; the backend reports what was delivered
  scene.onDose(async (ch: Channel, ml: number) => {
    const result = await api.manual_dose(ch, ml);
    if (!result.ok) {
      // e.g. still busy with the previous click; this click is not pumped or logged
      showStatusError(result.error || "Busy");
    }
  });

  // Cuvette rotator: a click on the robot rotates to its angle field, or reconnects it when offline
  scene.onRotate(async (deg: number) => {
    const result = await api.manual_rotate(deg);
    if (!result.ok) showStatusError(result.error || "Busy");
  });
  scene.onRotatorConnect(() => {
    api.rotator_connect(robotPort.value || null);
  });

  // Serial port dropdowns: "Auto" (value "") finds the device itself, or a port picked
  // by hand. The list is refreshed when the mouse or focus comes to a dropdown, so a
  // device plugged in after launch shows up.
  function fillPorts(select: HTMLSelectElement, ports: SerialPort[]): void {
    const chosen = select.value;
    const options = [new Option("Auto port", "")];
    for (const p of ports) {
      const option = new Option(p.description ? `${p.device} · ${p.description}` : p.device, p.device);
      option.title = p.description;
      options.push(option);
    }
    if (chosen && !ports.some((p) => p.device === chosen)) options.push(new Option(`${chosen} (unplugged)`, chosen));
    select.replaceChildren(...options);
    select.value = chosen;
  }
  async function refreshPorts(): Promise<void> {
    try {
      const ports = await api.list_ports();
      fillPorts(robotPort, ports);
      fillPorts(pumpPort, ports);
    } catch (e) {
      console.error(e);
    }
  }
  const robotPort = document.createElement("select");
  robotPort.id = "robot-port";
  robotPort.className = "port-select";
  robotPort.title = "Cuvette rotator serial port. Auto: find the board by its USB id";
  scene.mountRobotControl(robotPort);
  const pumpPort = document.getElementById("pump-port") as HTMLSelectElement;
  for (const select of [robotPort, pumpPort]) {
    select.addEventListener("pointerenter", refreshPorts);
    select.addEventListener("focus", refreshPorts);
  }
  fillPorts(robotPort, []);
  fillPorts(pumpPort, []);
  refreshPorts();
  robotPort.onchange = async () => {
    const result = await api.rotator_connect(robotPort.value || null);
    if (!result.ok) showStatusError(result.error || "Couldn't connect the cuvette rotator");
  };
  function updateRobotPort(): void {
    robotPort.disabled = rotatorState === "moving" || BUSY_STATUSES.includes(currentStatus);
  }

  // Tubing: the pump's max flow, and so how fast every dose runs, follows the tube size set on it
  const tubeInput = document.getElementById("tube-input") as HTMLInputElement;
  let tubeMm: number | null = null;     // the pump's value, restored after a refused entry
  let tubeSetting = false;
  const tubeLocked = () => tubeSetting || BUSY_STATUSES.includes(currentStatus);
  function showTubing(t: TubingInfo): void {
    tubeInput.min = String(t.min_mm);
    tubeInput.max = String(t.max_mm);
    tubeMm = t.mm;
    tubeInput.value = t.mm === null ? "" : String(t.mm);

    const flowEl = document.getElementById("tube-flow")!;
    const flows = Object.entries(t.max_ml_per_min);
    if (!flows.length) {
      flowEl.textContent = "pump offline";
      flowEl.title = t.error || "";
      return;
    }
    const values = flows.map(([, f]) => f);
    const lo = Math.min(...values), hi = Math.max(...values);
    flowEl.textContent = lo === hi ? `max ${lo} mL/min` : `max ${lo}–${hi} mL/min`;
    flowEl.title = flows.map(([ch, f]) => `${ch}: ${f} mL/min`).join("\n");
  }
  tubeInput.onchange = async () => {
    const mm = tubeInput.value.trim() === "" ? NaN : Number(tubeInput.value);
    if (!Number.isFinite(mm)) {
      tubeInput.value = tubeMm === null ? "" : String(tubeMm);
      return;
    }
    tubeSetting = true;
    tubeInput.disabled = true;
    try {
      const result = await api.set_tubing(mm);
      showTubing(result);
      if (!result.ok) showStatusError(result.error || "Couldn't set the tubing");
    } catch (e) {
      tubeInput.value = tubeMm === null ? "" : String(tubeMm);
      showStatusError(String(e));
    } finally {
      tubeSetting = false;
      tubeInput.disabled = tubeLocked();
    }
  };

  // Pump: detect at startup (real pump, else the mock), then read its tubing; the buttons redo that
  const redetectBtn = document.getElementById("btn-pump-redetect") as HTMLButtonElement;
  const logBtn = document.getElementById("btn-pump-log") as HTMLButtonElement;
  let pumpLogging = false;
  let pumpBusy = false;
  const pumpLocked = () => BUSY_STATUSES.includes(currentStatus);
  function updateRedetect(): void {
    redetectBtn.disabled = pumpPort.disabled = pumpBusy || pumpLocked();
    updateRobotPort();
  }
  function showPump(p: PumpInfo): void {
    pumpBadge.hidden = false;
    pumpBadge.title = p.message;
    if (p.mode === "mock") {
      pumpBadge.className = "mock-badge pump-mock";
      pumpBadge.textContent = p.message;
    } else {
      pumpBadge.className = "pump-ok";
      pumpBadge.textContent = "● pump";
    }
    pumpLogging = p.logging;
    logBtn.classList.toggle("active", p.logging);
    logBtn.setAttribute("aria-pressed", String(p.logging));
  }
  async function connectPump(): Promise<void> {
    pumpBusy = true;
    updateRedetect();
    try {
      const info = await api.pump_connect(pumpPort.value || null);
      showPump(info);
      if (!info.ok) showStatusError(info.error || "Couldn't detect the pump");
      showTubing(await api.get_tubing());
    } catch (e) {
      showStatusError(String(e));
    } finally {
      pumpBusy = false;
      updateRedetect();
    }
  }
  redetectBtn.onclick = connectPump;
  pumpPort.onchange = connectPump;
  logBtn.onclick = async () => {
    const info = await api.set_pump_logging(!pumpLogging);
    showPump(info);
    if (!info.ok) showStatusError(info.error || "Couldn't change logging");
  };
  connectPump();

  // -- Tubes: lengths, flushing and cleaning --------------------------------
  const flushInBtn = document.getElementById("btn-flush-in") as HTMLButtonElement;
  const flushOutBtn = document.getElementById("btn-flush-out") as HTMLButtonElement;
  const cleanBtn = document.getElementById("btn-clean") as HTMLButtonElement;
  const lengthsBtn = document.getElementById("btn-tube-lengths") as HTMLButtonElement;
  const label = (ch: Channel) => `${ch} (${currentPreset.solutions[ch]})`;
  const mlText = (ml: number) => `${ml.toFixed(2)} mL`;
  function minutesText(seconds: number): string {
    return seconds < 90 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
  }

  // The liquids A-C the program pumps (a channel with mL > 0 in any pump block)
  function usedSources(): Channel[] {
    const blocks = blocksEditor.getBlocks();
    return (["A", "B", "C"] as Channel[]).filter((ch) =>
      blocks.some((b) => b.type === "pump" && (b.pumps[ch]?.ml ?? 0) > 0));
  }

  function updateFlushInfo(): void {
    const info = document.getElementById("flush-info")!;
    const missing = CHANNELS.filter((ch) => tubeLengths[ch] === null);
    if (missing.length) {
      info.textContent = `set the tube lengths (${missing.join(", ")})`;
      info.title = "";
      return;
    }
    info.textContent = "flush " + CHANNELS.map((ch) => `${ch} ${flushMl(tubeLengths[ch]!).toFixed(1)}`).join(" · ") + " mL";
    info.title = CHANNELS.map((ch) => `${ch}: ${tubeLengths[ch]} mm tube = ${mlText(flushMl(tubeLengths[ch]!) - FLUSH_EXCESS_ML)} + ${FLUSH_EXCESS_ML} mL excess`).join("\n");
  }

  function updateFlushButtons(): void {
    const busy = BUSY_STATUSES.includes(currentStatus);
    for (const btn of [flushInBtn, flushOutBtn, cleanBtn, lengthsBtn]) btn.disabled = busy;
  }

  async function startService(call: Promise<{ ok: boolean; error?: string }>, note: { what: string; next: string } | null): Promise<void> {
    const result = await call;
    if (!result.ok) {
      showStatusError(result.error || "Busy");
      return;
    }
    afterService = note;
  }

  flushInBtn.onclick = async () => {
    const chs = usedSources();
    if (!chs.length) {
      alert("This program doesn't pump any liquid A-C, so there is nothing to flush in.");
      return;
    }
    const missing = chs.filter((ch) => tubeLengths[ch] === null);
    if (missing.length) {
      alert(`Set the tube length of ${missing.join(", ")} first (📏 Lengths).`);
      return;
    }
    const lines = chs.map((ch) => `  ${label(ch)}: ${mlText(flushMl(tubeLengths[ch]!))}`).join("\n");
    if (!confirm(`Flush the tubes of ${chs.join(", ")}.\n\nMove the outlets of ${chs.join(", ")} from the bowl to a waste cup now, then press OK.\n\nPumps:\n${lines}\n\n(Not counted in the totals.)`)) return;
    await startService(api.flush(chs), { what: "Flush", next: `Move the outlets of ${chs.join(", ")} back into the bowl.` });
  };

  flushOutBtn.onclick = async () => {
    if (tubeLengths.D === null) {
      alert("Set the tube length of D first (📏 Lengths).");
      return;
    }
    const ml = flushMl(tubeLengths.D);
    if (!confirm(`Flush tube D with the finished mixture.\n\nPumps ${mlText(ml)} from the bowl through the cuvette, so the bowl must hold at least that much mixture.\n\n(Not counted in the totals.)`)) return;
    await startService(api.flush(["D"]), null);
  };

  cleanBtn.onclick = async () => {
    const missing = CHANNELS.filter((ch) => tubeLengths[ch] === null);
    if (missing.length) {
      alert(`Set the tube length of ${missing.join(", ")} first (📏 Lengths).`);
      return;
    }
    const plan = cleanPlan(CHANNELS.map((ch) => tubeLengths[ch]!) as [number, number, number, number]);
    const water = (["A", "B", "C"] as Channel[]).map((ch, i) => `  ${label(ch)}: ${Math.ceil(plan.waterMl[i])} mL`).join("\n");
    if (!confirm(`Clean all tubes with water.\n\n1. Fill the beakers with at least:\n${water}\n2. Put every outlet in its normal place (A-C into the bowl).\n3. Make sure the cuvette's outlet goes to waste.\n\nIt rinses ${plan.steps.length / 2} rounds (A-C into the bowl, then D drains it) and takes about ${minutesText(plan.seconds)}.`)) return;
    await startService(api.clean(), { what: "Cleaning", next: "Put the beakers with your liquids back before the next run." });
  };

  lengthsBtn.onclick = () => showTubeLengthsDialog();

  function showTubeLengthsDialog(): void {
    const overlay = document.createElement("div");
    overlay.className = "save-dialog-overlay";
    document.body.appendChild(overlay);
    const dialog = document.createElement("div");
    dialog.className = "save-dialog tube-dialog";
    dialog.innerHTML = `
      <h3>Tube lengths</h3>
      <p class="tube-dialog-hint">From the beaker (or the bowl, for D) to the end of the tube, in mm. A flush pumps the tube's volume (⌀ ${TUBE_INNER_DIAMETER_MM} mm) plus ${FLUSH_EXCESS_ML} mL.</p>
      ${CHANNELS.map((ch) => `
        <label class="tube-row">
          <span class="tube-name">${label(ch)}</span>
          <input data-ch="${ch}" type="number" min="1" step="1" placeholder="mm">
          <span class="unit">mm</span>
          <span class="tube-ml" data-ml="${ch}"></span>
        </label>`).join("")}
      <div class="save-dialog-buttons">
        <button class="btn-cancel">Cancel</button>
        <button class="btn-save">Save</button>
      </div>
    `;
    document.body.appendChild(dialog);
    const inputs = [...dialog.querySelectorAll("input")] as HTMLInputElement[];
    const showMl = () => {
      for (const input of inputs) {
        const out = dialog.querySelector(`[data-ml="${input.dataset.ch}"]`)!;
        const mm = Number(input.value);
        out.textContent = input.value.trim() && !tubeLengthError(mm) ? `= ${mlText(flushMl(mm))}` : "";
      }
    };
    for (const input of inputs) {
      const v = tubeLengths[input.dataset.ch as Channel];
      input.value = v === null ? "" : String(v);
      input.oninput = showMl;
    }
    showMl();
    inputs[0].focus();

    const close = () => {
      overlay.remove();
      dialog.remove();
    };
    (dialog.querySelector(".btn-cancel") as HTMLButtonElement).onclick = close;
    (dialog.querySelector(".btn-save") as HTMLButtonElement).onclick = async () => {
      const mm: Partial<Record<Channel, number | null>> = {};
      for (const input of inputs) {
        const ch = input.dataset.ch as Channel;
        if (!input.value.trim()) {
          mm[ch] = null;
          continue;
        }
        const error = tubeLengthError(Number(input.value));
        if (error) {
          alert(`${ch}: ${error}`);
          input.focus();
          return;
        }
        mm[ch] = Number(input.value);
      }
      const result = await api.set_tube_lengths(mm);
      tubeLengths = result.mm;
      updateFlushInfo();
      if (!result.ok) {
        alert(result.error || "Couldn't save the tube lengths");
        return;
      }
      close();
    };
    dialog.onkeydown = (e) => {
      if (e.key === "Enter") (dialog.querySelector(".btn-save") as HTMLButtonElement).click();
      if (e.key === "Escape") close();
    };
  }

  api.get_tube_lengths().then((result) => {
    tubeLengths = result.mm;
    updateFlushInfo();
  }).catch(console.error);
  updateFlushInfo();

  // Control bar buttons
  document.getElementById("btn-rain")!.onclick = async () => {
    const blocks = blocksEditor.getBlocks();
    if (!blocks.length) {
      alert("Add some blocks first!");
      return;
    }

    // Same checks the backend applies
    const error = programError(blocks, rotatorState);
    if (error) {
      showStatusError(error);
      return;
    }

    const repeat = parseInt((document.getElementById("repeat-input") as HTMLInputElement).value) || 1;
    currentPreset.repeat = repeat;

    const result = await api.run_program(blocks, repeat);
    if (!result.ok) {
      alert(`Error: ${result.error}`);
    }
  };

  document.getElementById("btn-pause")!.onclick = async () => {
    const statusPill = document.getElementById("status-pill")!;
    if (statusPill.textContent?.includes("Paused")) {
      await api.resume_run();
    } else {
      await api.pause_run();
    }
  };

  document.getElementById("btn-stop")!.onclick = async () => {
    await api.stop();
  };

  // Repeat input change
  (document.getElementById("repeat-input") as HTMLInputElement).onchange = (e) => {
    const val = parseInt((e.target as HTMLInputElement).value) || 1;
    currentPreset.repeat = Math.max(1, Math.min(99, val));
    updateProgramInfo();
  };

  // Event handling
  onEvent((event: BackendEvent) => {
    const statusPill = document.getElementById("status-pill")!;
    const progressFill = document.getElementById("progress-fill") as HTMLElement;
    const rainBtn = document.getElementById("btn-rain") as HTMLButtonElement;
    const pauseBtn = document.getElementById("btn-pause") as HTMLButtonElement;
    const stopBtn = document.getElementById("btn-stop") as HTMLButtonElement;

    if (event.type === "status") {
      const status = event.status;
      statusPill.textContent = status === "raining" ? "Raining..." :
                                status === "paused" ? "Paused" :
                                status === "dosing" ? "Dosing..." :
                                status === "rotating" ? "Rotating..." :
                                status === "flushing" ? "Flushing..." :
                                status === "cleaning" ? (event.message || "Cleaning...") :
                                status === "done" ? "Done ✨" :
                                status === "stopped" ? "Stopped" :
                                status === "error" ? "Error" :
                                "Ready";

      statusPill.className = `status-pill status-${status}`;

      const running = ["raining", "dosing", "rotating", "flushing", "cleaning"].includes(status);
      const paused = status === "paused";

      rainBtn.disabled = running || paused;
      pauseBtn.textContent = paused ? "▶ Resume" : "⏸ Pause";
      // A manual rotate (the board finishes each move), a flush or a clean can't be paused
      pauseBtn.disabled = (!running && !paused) || ["rotating", "flushing", "cleaning"].includes(status);
      stopBtn.disabled = !running && !paused;

      blocksEditor.setLocked(running || paused);
      currentStatus = status;
      (document.getElementById("tube-input") as HTMLInputElement).disabled = tubeLocked();
      updateRedetect();
      updateFlushButtons();

      if (paused) {
        scene.setPaused(true);
      } else if (running) {
        scene.setPaused(false);
      }

      if (status === "stopped" || status === "done" || status === "error") {
        scene.stopAll();
        blocksEditor.highlight(null);
        isRunning = false;
      }

      // A flush or clean ended (or was stopped): tell what to do next
      if (afterService && (status === "ready" || status === "stopped" || status === "error")) {
        const leftover = afterService.what === "Cleaning" && status !== "ready" ? "The bowl may still hold rinse water. " : "";
        const note = `${afterService.what} ${status === "ready" ? "done" : status}. ${leftover}${afterService.next}`;
        afterService = null;
        setTimeout(() => alert(note), 50);
      }

      if (event.message) {
        statusPill.title = event.message;
      }
    } else if (event.type === "block_start") {
      isRunning = true;
      blocksEditor.highlight(event.index);

      const block = event.block;
      if (block.type === "pump") {
        // For each active channel, start rain or pour for as long as it pumps
        const channels: Channel[] = ["A", "B", "C", "D"];
        channels.forEach((ch) => {
          const pump = block.pumps[ch];
          const s = pump ? pumpSeconds(pump.ml) : 0;
          if (s > 0) {
            if (ch === "D") {
              scene.startPour(pump.ml, s);
            } else {
              scene.startRain(ch as any, pump.ml, s);
            }
          }
        });
      }
    } else if (event.type === "block_end") {
      // Block ended
    } else if (event.type === "progress") {
      progressFill.style.width = `${event.fraction * 100}%`;
    } else if (event.type === "dose_start") {
      const s = pumpSeconds(event.ml);
      if (event.channel === "D") scene.startPour(event.ml, s);
      else scene.startRain(event.channel, event.ml, s);
    } else if (event.type === "flush_start") {
      // Only D's flush is in the scene: it takes the mixture from the bowl. A-C flush into a waste cup.
      const d = event.volumes.D;
      if (d) scene.startPour(d, pumpSeconds(d));
    } else if (event.type === "pumped") {
      sessionLog.push({ time: new Date(), channel: event.channel, ml: event.ml, source: event.source });
      renderSessionLog();
    } else if (event.type === "rotator") {
      rotatorState = event.state;
      scene.setRotator(event.state, event.deg, event.target, event.message);
      updateRobotPort();
    }
  });

  // Initial setup
  updateUI();
  // Look for the cuvette rotator board and home it (progress arrives as `rotator` events)
  api.rotator_connect();
}

// Start the app
init().catch(console.error);
