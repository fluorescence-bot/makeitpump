import "@fontsource/m-plus-rounded-1c/400.css";
import "@fontsource/m-plus-rounded-1c/700.css";
import "@fontsource/m-plus-rounded-1c/800.css";
import "./styles.css";

import { getApi, onEvent, isMock } from "./bridge";
import { createBlockEditor } from "./blocks";
import { createScene } from "./scene";
import type { BackendApi, BackendEvent, Block, Preset, Channel, RunStatus } from "./types";

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
      <button id="btn-pour" class="big-btn pour">Make it POUR! 🫗</button>
      <div class="pour-inputs">
        <input id="pour-ml" type="number" placeholder="mL" step="0.1" min="0">
        <span>mL</span>
        <input id="pour-s" type="number" value="10" placeholder="s" step="0.5" min="0">
        <span>s</span>
      </div>
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

    let totalIn = 0;
    let totalOut = 0;
    let totalTime = 0;

    blocks.forEach((block) => {
      if (block.type === "pump") {
        // Sum A, B, C for "in"
        totalIn += (block.pumps.A?.ml || 0) + (block.pumps.B?.ml || 0) + (block.pumps.C?.ml || 0);
        // Sum D for "out"
        totalOut += block.pumps.D?.ml || 0;
        // Duration is max(s) across all channels
        const maxS = Math.max(
          block.pumps.A?.s || 0,
          block.pumps.B?.s || 0,
          block.pumps.C?.s || 0,
          block.pumps.D?.s || 0
        );
        totalTime += maxS;
      } else {
        totalTime += block.s;
      }
    });

    // Multiply total time by repeat
    const totalTimeWithRepeat = totalTime * currentPreset.repeat;
    const minutes = Math.floor(totalTimeWithRepeat / 60);
    const seconds = Math.round(totalTimeWithRepeat % 60);
    const timeStr = `${minutes}:${seconds.toString().padStart(2, "0")}`;

    info.textContent = `In: ${totalIn.toFixed(1)} mL · Out: ${totalOut.toFixed(1)} mL · Time: ${timeStr}`;
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
    updatePourDefaults();
  }

  function updatePourDefaults(): void {
    const bowl = scene.getBowl();
    const pourMl = document.getElementById("pour-ml") as HTMLInputElement;
    if (document.activeElement !== pourMl) pourMl.value = bowl.total.toFixed(1);
  }
  // keep the POUR amount in sync with what is in the bowl
  setInterval(updatePourDefaults, 500);

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
  });

  // Manual pump control
  scene.onManualPress(async (ch: Channel, active: boolean) => {
    // Ignore if a program or pour is running
    if (currentStatus === "raining" || currentStatus === "pouring" || currentStatus === "paused") {
      return;
    }
    if (active) {
      const result = await api.manual_pump_start(ch);
      if (!result.ok) {
        return;
      }
    } else {
      await api.manual_pump_stop(ch);
    }
  });

  // Validation helper
  function validateBlocks(blocks: Block[]): { ok: boolean; message?: string } {
    for (const block of blocks) {
      if (block.type === "pump") {
        const channels: Channel[] = ["A", "B", "C", "D"];
        for (const ch of channels) {
          const pump = block.pumps[ch];
          if (pump) {
            if (pump.ml < 0 || pump.s < 0) {
              return { ok: false, message: `${ch}: negative value` };
            }
            if (pump.ml > 0 && pump.s === 0) {
              return { ok: false, message: `${ch}: mL set but s = 0` };
            }
          }
        }
      }
    }
    return { ok: true };
  }

  // Control bar buttons
  document.getElementById("btn-rain")!.onclick = async () => {
    const blocks = blocksEditor.getBlocks();
    if (!blocks.length) {
      alert("Add some blocks first!");
      return;
    }

    const validation = validateBlocks(blocks);
    if (!validation.ok) {
      const statusPill = document.getElementById("status-pill")!;
      statusPill.textContent = validation.message || "Invalid";
      statusPill.className = "status-pill status-error";
      statusPill.title = validation.message || "Invalid block";
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

  document.getElementById("btn-pour")!.onclick = async () => {
    const mlInput = document.getElementById("pour-ml") as HTMLInputElement;
    const sInput = document.getElementById("pour-s") as HTMLInputElement;

    let ml = parseFloat(mlInput.value);
    if (isNaN(ml)) {
      const bowl = scene.getBowl();
      ml = bowl.total;
    }

    const s = parseFloat(sInput.value) || 15;

    if (ml <= 0 || s <= 0) {
      alert("Enter valid mL and seconds");
      return;
    }

    const result = await api.pour(ml, s);
    if (!result.ok) {
      alert(`Error: ${result.error}`);
    }
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
    const pourBtn = document.getElementById("btn-pour") as HTMLButtonElement;

    if (event.type === "status") {
      const status = event.status;
      currentStatus = status;
      statusPill.textContent = status === "raining" ? "Raining..." :
                                status === "paused" ? "Paused" :
                                status === "pouring" ? "Pouring..." :
                                status === "done" ? "Done ✨" :
                                status === "stopped" ? "Stopped" :
                                status === "error" ? "Error" :
                                "Ready";

      statusPill.className = `status-pill status-${status}`;

      const running = status === "raining" || status === "pouring";
      const paused = status === "paused";

      rainBtn.disabled = running || paused;
      pauseBtn.textContent = paused ? "▶ Resume" : "⏸ Pause";
      pauseBtn.disabled = !running && !paused;
      stopBtn.disabled = !running && !paused;
      pourBtn.disabled = running || paused;

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
        // For each channel with s > 0, start rain or pour
        const channels: Channel[] = ["A", "B", "C", "D"];
        channels.forEach((ch) => {
          const pump = block.pumps[ch];
          if (pump && pump.s > 0) {
            if (ch === "D") {
              scene.startPour(pump.ml, pump.s);
            } else {
              scene.startRain(ch as any, pump.ml, pump.s);
            }
          }
        });
      }
    } else if (event.type === "block_end") {
      // Block ended
    } else if (event.type === "progress") {
      progressFill.style.width = `${event.fraction * 100}%`;
    } else if (event.type === "pour_start") {
      scene.startPour(event.ml, event.s);
    } else if (event.type === "manual") {
      scene.setManual(event.channel, event.active);
    }
  });

  // Initial setup
  updateUI();
}

// Start the app
init().catch(console.error);
