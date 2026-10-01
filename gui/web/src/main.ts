import "@fontsource/m-plus-rounded-1c/400.css";
import "@fontsource/m-plus-rounded-1c/700.css";
import "@fontsource/m-plus-rounded-1c/800.css";
// Bundled emoji font: Linux often has none (emoji rendered as blank space).
import "@fontsource/noto-color-emoji/400.css";
import "./styles.css";

import { getApi, onEvent, isMock, programError } from "./bridge";
import { pumpSeconds } from "./flow";
import { createBlockEditor } from "./blocks";
import { createScene } from "./scene";
import { computeTotals } from "./totals";
import { sessionCsv, sessionTotals, type LogEntry } from "./sessionlog";
import type { BackendApi, BackendEvent, Preset, Channel, RotatorState, RunStatus } from "./types";

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
  `;
  programPanel.appendChild(repeatControl);

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

  if (isMock()) {
    const mockBadge = document.createElement("div");
    mockBadge.className = "mock-badge";
    mockBadge.textContent = "demo mode";
    controlBar.appendChild(mockBadge);
  }

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

  // Manual dosing: each click pumps that channel's mL-per-click at its speed; the backend reports what was delivered
  scene.onDose(async (ch: Channel, ml: number, speed: number) => {
    const result = await api.manual_dose(ch, ml, speed);
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
    api.rotator_connect();
  });

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
      currentStatus = status;
      statusPill.textContent = status === "raining" ? "Raining..." :
                                status === "paused" ? "Paused" :
                                status === "dosing" ? "Dosing..." :
                                status === "rotating" ? "Rotating..." :
                                status === "done" ? "Done ✨" :
                                status === "stopped" ? "Stopped" :
                                status === "error" ? "Error" :
                                "Ready";

      statusPill.className = `status-pill status-${status}`;

      const running = status === "raining" || status === "dosing" || status === "rotating";
      const paused = status === "paused";

      rainBtn.disabled = running || paused;
      pauseBtn.textContent = paused ? "▶ Resume" : "⏸ Pause";
      // A manual rotate can't be paused (the board finishes each move)
      pauseBtn.disabled = (!running && !paused) || status === "rotating";
      stopBtn.disabled = !running && !paused;

      blocksEditor.setLocked(running || paused);

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
          const s = pump ? pumpSeconds(pump.ml, pump.speed) : 0;
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
      const s = pumpSeconds(event.ml, event.speed);
      if (event.channel === "D") scene.startPour(event.ml, s);
      else scene.startRain(event.channel, event.ml, s);
    } else if (event.type === "pumped") {
      sessionLog.push({ time: new Date(), channel: event.channel, ml: event.ml, source: event.source });
      renderSessionLog();
    } else if (event.type === "rotator") {
      rotatorState = event.state;
      scene.setRotator(event.state, event.deg, event.target, event.message);
    }
  });

  // Initial setup
  updateUI();
  // Look for the cuvette rotator board and home it (progress arrives as `rotator` events)
  api.rotator_connect();
}

// Start the app
init().catch(console.error);
