import Sortable from "sortablejs";
import type { Block, PumpBlock, PauseBlock, Channel, ChannelPump } from "./types";

interface CreateBlockEditorOpts {
  getNames: () => Record<Channel, string>;
  onChange: (blocks: Block[]) => void;
}

interface BlockEditor {
  getBlocks(): Block[];
  setBlocks(b: Block[]): void;
  highlight(index: number | null): void;
  setLocked(locked: boolean): void;
  refreshNames(): void;
}

// Normalize a pump block: handle legacy {channel, ml, s} and missing channels
export function normalizePumpBlock(block: any): PumpBlock {
  let pumps: Record<Channel, ChannelPump> = {
    A: { ml: 0, s: 0 },
    B: { ml: 0, s: 0 },
    C: { ml: 0, s: 0 },
    D: { ml: 0, s: 0 },
  };

  if (block.pumps && typeof block.pumps === "object") {
    // New format: fill in missing channels
    const channels: Channel[] = ["A", "B", "C", "D"];
    channels.forEach((ch) => {
      const p = block.pumps[ch];
      const ml = typeof p?.ml === "number" && isFinite(p.ml) ? p.ml : 0;
      const s = typeof p?.s === "number" && isFinite(p.s) ? p.s : 0;
      pumps[ch] = { ml, s };
    });
  } else if (block.channel && (block.ml !== undefined || block.s !== undefined)) {
    // Legacy format: convert to new
    const ch = block.channel as Channel;
    const ml = typeof block.ml === "number" && isFinite(block.ml) ? block.ml : 0;
    const s = typeof block.s === "number" && isFinite(block.s) ? block.s : 0;
    pumps[ch] = { ml, s };
  }

  return {
    id: block.id,
    type: "pump" as const,
    pumps,
  };
}

export function createBlockEditor(
  listEl: HTMLElement,
  paletteEl: HTMLElement,
  opts: CreateBlockEditorOpts
): BlockEditor {
  let blocks: Block[] = [];
  let locked = false;

  function generateId(): string {
    return Math.random().toString(36).substr(2, 9);
  }

  function renderBlocks(): void {
    listEl.innerHTML = "";

    if (blocks.length === 0) {
      const empty = document.createElement("div");
      empty.className = "blocks-empty";
      empty.textContent = "Drag a 💧 Pump or ⏸ Pause here ☁️";
      listEl.appendChild(empty);
      return;
    }

    blocks.forEach((block, i) => {
      const card = document.createElement("div");
      card.className = `block-card block-${block.type}`;
      card.dataset.index = String(i);

      const indexBadge = document.createElement("div");
      indexBadge.className = "block-index";
      indexBadge.textContent = String(i + 1);

      const dragHandle = document.createElement("div");
      dragHandle.className = "block-drag-handle";
      dragHandle.textContent = "⋮⋮";

      if (block.type === "pump") {
        const pblock = block as PumpBlock;
        const names = opts.getNames();
        const channels: Channel[] = ["A", "B", "C", "D"];

        // Calculate max duration
        const maxS = Math.max(...channels.map((ch) => pblock.pumps[ch].s));

        // Header with drag handle, index, duration, actions
        const header = document.createElement("div");
        header.className = "pump-header";
        header.appendChild(indexBadge);
        header.appendChild(dragHandle);

        const headerLabel = document.createElement("div");
        headerLabel.className = "pump-header-label";
        headerLabel.innerHTML = `<span class="pump-emoji">💧</span><span class="pump-title">Pump</span><span class="pump-duration">· ${maxS} s</span>`;
        header.appendChild(headerLabel);

        const actions = document.createElement("div");
        actions.className = "block-actions";

        const dupBtn = document.createElement("button");
        dupBtn.textContent = "📋";
        dupBtn.disabled = locked;
        dupBtn.title = "Duplicate";
        dupBtn.onclick = () => {
          const newBlock = { ...block, id: generateId() };
          blocks.splice(i + 1, 0, newBlock);
          opts.onChange(blocks);
          renderBlocks();
        };
        actions.appendChild(dupBtn);

        const delBtn = document.createElement("button");
        delBtn.textContent = "🗑";
        delBtn.disabled = locked;
        delBtn.title = "Delete";
        delBtn.onclick = () => {
          blocks.splice(i, 1);
          opts.onChange(blocks);
          renderBlocks();
        };
        actions.appendChild(delBtn);

        header.appendChild(actions);
        card.appendChild(header);

        // Grid: columns for A, B, C, D and rows for Letter, mL, s
        const grid = document.createElement("div");
        grid.className = "pump-grid";

        // Row headers (mL and s labels)
        const emptyCorner = document.createElement("div");
        emptyCorner.className = "pump-grid-empty";
        grid.appendChild(emptyCorner);

        // Letter row
        const pillCells: Map<Channel, HTMLElement> = new Map();
        const mlCells: Map<Channel, HTMLElement> = new Map();
        const sCells: Map<Channel, HTMLElement> = new Map();

        channels.forEach((ch) => {
          const cell = document.createElement("div");
          cell.className = `pump-grid-cell pump-grid-letter pump-ch-${ch.toLowerCase()}`;
          const pill = document.createElement("div");
          pill.className = "pump-pill";
          const chText = document.createElement("span");
          chText.textContent = ch;
          const nameText = document.createElement("span");
          nameText.className = "pump-pill-name";
          nameText.textContent = names[ch] || "?";
          pill.appendChild(chText);
          pill.appendChild(document.createTextNode(" · "));
          pill.appendChild(nameText);
          cell.appendChild(pill);

          pillCells.set(ch, cell);
          grid.appendChild(cell);
        });

        // mL row
        const mlRowLabel = document.createElement("div");
        mlRowLabel.className = "pump-grid-label";
        mlRowLabel.textContent = "mL";
        grid.appendChild(mlRowLabel);

        channels.forEach((ch) => {
          const cell = document.createElement("div");
          cell.className = "pump-grid-cell pump-grid-input";
          mlCells.set(ch, cell);
          const pump = pblock.pumps[ch];
          const input = document.createElement("input");
          input.type = "number";
          input.step = "0.1";
          input.min = "0";
          input.disabled = locked;
          input.value = String(pump.ml);

          input.onchange = () => {
            const val = parseFloat(input.value);
            const newPumps = { ...pblock.pumps };
            newPumps[ch] = {
              ...newPumps[ch],
              ml: isNaN(val) || val < 0 ? 0 : val,
            };
            const newBlock: PumpBlock = { ...pblock, pumps: newPumps };
            blocks[i] = newBlock;
            opts.onChange(blocks);
            renderBlocks();
          };

          cell.appendChild(input);
          grid.appendChild(cell);
        });

        // s row
        const sRowLabel = document.createElement("div");
        sRowLabel.className = "pump-grid-label";
        sRowLabel.textContent = "s";
        grid.appendChild(sRowLabel);

        channels.forEach((ch) => {
          const cell = document.createElement("div");
          cell.className = "pump-grid-cell pump-grid-input";
          sCells.set(ch, cell);
          const pump = pblock.pumps[ch];
          const input = document.createElement("input");
          input.type = "number";
          input.step = "0.5";
          input.min = "0";
          input.disabled = locked;
          input.value = String(pump.s);

          input.onchange = () => {
            const val = parseFloat(input.value);
            const newPumps = { ...pblock.pumps };
            newPumps[ch] = {
              ...newPumps[ch],
              s: isNaN(val) || val < 0 ? 0 : val,
            };
            const newBlock: PumpBlock = { ...pblock, pumps: newPumps };
            blocks[i] = newBlock;
            opts.onChange(blocks);
            renderBlocks();
          };

          cell.appendChild(input);
          grid.appendChild(cell);
        });

        // Apply dimmed and danger states
        channels.forEach((ch) => {
          const pump = pblock.pumps[ch];
          const isDimmed = pump.ml === 0 && pump.s === 0;
          const isDanger = pump.ml < 0 || pump.s < 0 || (pump.ml > 0 && pump.s === 0);

          const pillCell = pillCells.get(ch);
          const mlCell = mlCells.get(ch);
          const sCell = sCells.get(ch);

          if (isDanger) {
            mlCell?.classList.add("pump-grid-danger");
            sCell?.classList.add("pump-grid-danger");
          }
          if (isDimmed && !isDanger) {
            pillCell?.classList.add("pump-grid-dimmed");
            mlCell?.classList.add("pump-grid-dimmed");
            sCell?.classList.add("pump-grid-dimmed");
          }
        });

        card.appendChild(grid);
      } else {
        // Pause block
        const pauseBlock = block as PauseBlock;

        const content = document.createElement("div");
        content.className = "block-content block-pause";

        card.appendChild(indexBadge);
        card.appendChild(dragHandle);

        const pauseLabel = document.createElement("div");
        pauseLabel.className = "pause-label";
        pauseLabel.innerHTML = `<span class="pause-emoji">⏸</span><span class="pause-title">Pause</span>`;
        content.appendChild(pauseLabel);

        const secGroup = document.createElement("div");
        secGroup.className = "block-field";
        const secLabel = document.createElement("label");
        secLabel.textContent = "for";
        const secInput = document.createElement("input");
        secInput.type = "number";
        secInput.step = "0.5";
        secInput.min = "0";
        secInput.disabled = locked;
        secInput.value = String(pauseBlock.s);
        secInput.onchange = () => {
          const val = parseFloat(secInput.value);
          const newBlock = { ...pauseBlock, s: isNaN(val) || val < 0 ? 0 : val };
          blocks[i] = newBlock;
          opts.onChange(blocks);
          renderBlocks();
        };
        const secUnit = document.createElement("span");
        secUnit.textContent = "s";
        secGroup.appendChild(secLabel);
        secGroup.appendChild(secInput);
        secGroup.appendChild(secUnit);
        content.appendChild(secGroup);

        const actions = document.createElement("div");
        actions.className = "block-actions";

        const dupBtn = document.createElement("button");
        dupBtn.textContent = "📋";
        dupBtn.disabled = locked;
        dupBtn.title = "Duplicate";
        dupBtn.onclick = () => {
          const newBlock = { ...block, id: generateId() };
          blocks.splice(i + 1, 0, newBlock);
          opts.onChange(blocks);
          renderBlocks();
        };
        actions.appendChild(dupBtn);

        const delBtn = document.createElement("button");
        delBtn.textContent = "🗑";
        delBtn.disabled = locked;
        delBtn.title = "Delete";
        delBtn.onclick = () => {
          blocks.splice(i, 1);
          opts.onChange(blocks);
          renderBlocks();
        };
        actions.appendChild(delBtn);

        content.appendChild(actions);
        card.appendChild(content);
      }

      listEl.appendChild(card);
    });
  }

  function createPaletteUI(): void {
    paletteEl.innerHTML = "";
    paletteEl.className = "blocks-palette";

    const pumpTile = document.createElement("div");
    pumpTile.className = "palette-tile";
    pumpTile.textContent = "💧 Pump";
    pumpTile.onclick = () => {
      const newBlock: PumpBlock = {
        id: generateId(),
        type: "pump",
        pumps: {
          A: { ml: 1, s: 5 },
          B: { ml: 0, s: 0 },
          C: { ml: 0, s: 0 },
          D: { ml: 0, s: 0 },
        },
      };
      blocks.push(newBlock);
      opts.onChange(blocks);
      renderBlocks();
    };

    const pauseTile = document.createElement("div");
    pauseTile.className = "palette-tile";
    pauseTile.textContent = "⏸ Pause";
    pauseTile.onclick = () => {
      const newBlock: PauseBlock = {
        id: generateId(),
        type: "pause",
        s: 5,
      };
      blocks.push(newBlock);
      opts.onChange(blocks);
      renderBlocks();
    };

    paletteEl.appendChild(pumpTile);
    paletteEl.appendChild(pauseTile);
  }

  // Set up SortableJS for the palette (clone source)
  Sortable.create(paletteEl, {
    group: {
      name: "blocks",
      pull: "clone",
      put: false,
    },
    sort: false,
    animation: 150,
    onEnd: () => {
      // Clone action handled by list
    },
  });

  // Set up SortableJS for the list (drop target)
  Sortable.create(listEl, {
    group: {
      name: "blocks",
      pull: false,
      put: ["blocks"],
    },
    sort: true,
    animation: 150,
    onAdd: (evt) => {
      // A new item was added from the palette
      const clone = evt.item as HTMLElement;

      // Determine what type of block was dragged
      let newBlock: Block;
      if (clone.textContent?.includes("💧")) {
        newBlock = {
          id: generateId(),
          type: "pump",
          pumps: {
            A: { ml: 1, s: 5 },
            B: { ml: 0, s: 0 },
            C: { ml: 0, s: 0 },
            D: { ml: 0, s: 0 },
          },
        };
      } else {
        newBlock = {
          id: generateId(),
          type: "pause",
          s: 5,
        };
      }

      // Remove the clone element and add our real block
      clone.remove();
      const idx = evt.newIndex || blocks.length;
      blocks.splice(idx, 0, newBlock);
      opts.onChange(blocks);
      renderBlocks();
    },
    onEnd: () => {
      // Update block order after drag
      opts.onChange(blocks);
      renderBlocks();
    },
  });

  createPaletteUI();
  renderBlocks();

  return {
    getBlocks(): Block[] {
      return blocks;
    },
    setBlocks(b: Block[]): void {
      // Normalize pump blocks: handle legacy format and missing channels
      blocks = b.map((block) => {
        if (block.type === "pump") {
          return normalizePumpBlock(block);
        }
        return block;
      });
      renderBlocks();
    },
    highlight(index: number | null): void {
      document.querySelectorAll(".block-card").forEach((el) => {
        el.classList.remove("block-highlighted");
      });
      if (index !== null && index >= 0 && index < blocks.length) {
        const cards = document.querySelectorAll(".block-card");
        cards[index]?.classList.add("block-highlighted");
      }
    },
    setLocked(locked_: boolean): void {
      locked = locked_;
      renderBlocks();
    },
    refreshNames(): void {
      renderBlocks();
    },
  };
}
