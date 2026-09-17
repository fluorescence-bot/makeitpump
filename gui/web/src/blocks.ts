import Sortable from "sortablejs";
import type { Block, PumpBlock, PauseBlock, Channel } from "./types";

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
      if (block.type === "pump") {
        const channelClass = `block-ch-${(block as PumpBlock).channel.toLowerCase()}`;
        card.classList.add(channelClass);
      } else {
        card.classList.add("block-pause");
      }
      card.dataset.index = String(i);

      const indexBadge = document.createElement("div");
      indexBadge.className = "block-index";
      indexBadge.textContent = String(i + 1);

      const dragHandle = document.createElement("div");
      dragHandle.className = "block-drag-handle";
      dragHandle.textContent = "⋮⋮";

      const content = document.createElement("div");
      content.className = "block-content";

      if (block.type === "pump") {
        const pblock = block as PumpBlock;
        const names = opts.getNames();

        const channelGroup = document.createElement("div");
        channelGroup.className = "block-field";

        const channelSelect = document.createElement("select");
        channelSelect.disabled = locked;
        channelSelect.className = "block-channel-select";
        const channels: Channel[] = ["A", "B", "C", "D"];
        channels.forEach((ch) => {
          const opt = document.createElement("option");
          opt.value = ch;
          opt.textContent = `${ch} · ${names[ch] || "?"}`;
          if (pblock.channel === ch) opt.selected = true;
          channelSelect.appendChild(opt);
        });

        channelSelect.onchange = () => {
          const newBlock = { ...pblock, channel: channelSelect.value as Channel };
          blocks[i] = newBlock;
          opts.onChange(blocks);
          renderBlocks();
        };

        channelGroup.appendChild(channelSelect);
        content.appendChild(channelGroup);

        const mlGroup = document.createElement("div");
        mlGroup.className = "block-field";
        const mlLabel = document.createElement("label");
        mlLabel.textContent = "mL";
        const mlInput = document.createElement("input");
        mlInput.type = "number";
        mlInput.step = "0.1";
        mlInput.min = "0";
        mlInput.disabled = locked;
        mlInput.value = String(pblock.ml);
        mlInput.onchange = () => {
          const val = parseFloat(mlInput.value);
          const newBlock = { ...pblock, ml: isNaN(val) ? 0 : val };
          blocks[i] = newBlock;
          opts.onChange(blocks);
          renderBlocks();
        };
        mlGroup.appendChild(mlLabel);
        mlGroup.appendChild(mlInput);
        content.appendChild(mlGroup);

        const secGroup = document.createElement("div");
        secGroup.className = "block-field";
        const secLabel = document.createElement("label");
        secLabel.textContent = "s";
        const secInput = document.createElement("input");
        secInput.type = "number";
        secInput.step = "0.5";
        secInput.min = "0";
        secInput.disabled = locked;
        secInput.value = String(pblock.s);
        secInput.onchange = () => {
          const val = parseFloat(secInput.value);
          const newBlock = { ...pblock, s: isNaN(val) ? 0 : val };
          blocks[i] = newBlock;
          opts.onChange(blocks);
          renderBlocks();
        };
        secGroup.appendChild(secLabel);
        secGroup.appendChild(secInput);
        content.appendChild(secGroup);
      } else {
        const pauseBlock = block as PauseBlock;

        const secGroup = document.createElement("div");
        secGroup.className = "block-field";
        const secLabel = document.createElement("label");
        secLabel.textContent = "Pause for";
        const secInput = document.createElement("input");
        secInput.type = "number";
        secInput.step = "0.5";
        secInput.min = "0";
        secInput.disabled = locked;
        secInput.value = String(pauseBlock.s);
        secInput.onchange = () => {
          const val = parseFloat(secInput.value);
          const newBlock = { ...pauseBlock, s: isNaN(val) ? 0 : val };
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
      }

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

      card.appendChild(indexBadge);
      card.appendChild(dragHandle);
      card.appendChild(content);
      card.appendChild(actions);
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
        channel: "A",
        ml: 5,
        s: 10,
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
          channel: "A",
          ml: 5,
          s: 10,
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
      blocks = b;
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
      document.querySelectorAll<HTMLInputElement>(".block-card input, .block-card select, .block-card button")
        .forEach((el) => {
          if (el.classList.contains("block-drag-handle")) return;
          el.disabled = locked;
        });
      if (locked) {
        document.querySelector(".blocks-palette")?.classList.add("palette-locked");
      } else {
        document.querySelector(".blocks-palette")?.classList.remove("palette-locked");
      }
    },
    refreshNames(): void {
      renderBlocks();
    },
  };
}
