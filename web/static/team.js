(function () {
  const cardsContainer = document.getElementById("team-cards");
  const emptyContainer = document.getElementById("team-empty-slots");
  const modal = document.getElementById("team-modal");
  const closeBtn = document.getElementById("team-modal-close");
  const pickerGrid = document.getElementById("team-picker-grid");
  const search = document.getElementById("team-search");
  const saveBtn = document.getElementById("team-save");
  const status = document.getElementById("team-status");

  const configModal = document.getElementById("config-modal");
  const configClose = document.getElementById("config-modal-close");
  const configTitle = document.getElementById("config-title");
  const configMoves = document.getElementById("config-moves");
  const configAbilities = document.getElementById("config-abilities");
  const configSave = document.getElementById("config-save");
  const configStatus = document.getElementById("config-status");

  if (!cardsContainer) return;

  const TEAM_SIZE = 6;
  const initial = JSON.parse(document.getElementById("team-data").textContent || "[]");
  const teamState = Array.from({ length: TEAM_SIZE }, (_, i) => initial[i] || null);

  let allSpecies = [];
  let activeSlot = null;
  let configSlot = null;
  let configSelectedMoves = [];
  let configSelectedAbility = null;

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function moveCard(move) {
    if (!move) return "";
    return `
      <div class="move-card type-${move.type}">
        <div class="move-card-name">${move.name}</div>
        <div class="move-card-meta">${move.type} · ${move.category}${move.pp != null ? ` · ${move.pp} PP` : ""}</div>
      </div>`;
  }

  function statBars(stats) {
    return (stats || [])
      .map(
        (s) => `
        <div class="stat-bar-row">
          <span class="stat-bar-label">${s.label}</span>
          <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${Math.min(100, (s.at_level_100 / 400) * 100)}%"></div></div>
          <span class="stat-bar-value">${s.at_level_100} <span class="muted">(IV ${s.iv})</span></span>
        </div>`
      )
      .join("");
  }

  function renderTeamCard(mon, index) {
    const card = document.createElement("div");
    card.className = "card team-card";
    const moves = mon.moves && mon.moves.length ? mon.moves : [];
    const moveCells = Array.from({ length: 4 }, (_, i) => moveCard(moves[i]) || `<div class="move-card" style="opacity:0.4;"><div class="move-card-name">—</div></div>`).join("");

    card.innerHTML = `
      <div class="team-card-header">
        <div><span class="section-label">Slot ${index + 1}</span></div>
        <button class="btn-secondary config-btn" data-index="${index}">Configure →</button>
      </div>
      <div class="team-card-mon">
        <img src="${mon.artwork}" alt="${mon.name}">
        <h3>${mon.name}</h3>
        <div class="favorite-types" style="justify-content:center;">${typeBadges(mon.types)}</div>
      </div>
      <div class="move-grid">${moveCells}</div>
      <div class="team-card-side">
        <div class="team-card-side-label">Stats · Level 100</div>
        <div class="stat-bars">${statBars(mon.base_stats)}</div>
      </div>
      <div class="team-card-footer">
        <span class="ability-badge">${mon.ability || "No ability set"}</span>
        <button class="link-danger remove-btn" data-index="${index}">Remove from team</button>
      </div>
    `;

    card.querySelector(".config-btn").addEventListener("click", () => openConfig(index));
    card.querySelector(".remove-btn").addEventListener("click", () => {
      teamState[index] = null;
      render();
      markDirty();
    });

    card.querySelectorAll(".move-grid .move-card").forEach((cell, i) => {
      if (moves[i]) window.MoveTooltip.attach(cell, moves[i]);
    });

    return card;
  }

  // ---------- Lineup strip (drag to reorder) ----------
  // Pointer events rather than native HTML5 drag-and-drop, which doesn't fire
  // for touch input — this way reordering works the same with a mouse or a
  // finger. Dropping onto a filled slot swaps the two; onto an empty slot moves.

  const lineup = document.getElementById("team-lineup");
  let drag = null;

  function markDirty() {
    status.textContent = "Lineup changed — click Save Team to keep it.";
    status.classList.remove("error");
    saveBtn.classList.add("is-attention");
  }

  function renderLineup() {
    lineup.innerHTML = "";
    teamState.forEach((mon, i) => {
      const tile = document.createElement("div");
      tile.className = "lineup-slot" + (mon ? " is-filled" : " is-empty");
      tile.dataset.index = i;
      tile.innerHTML = mon
        ? `<span class="lineup-slot-num">${i + 1}</span>
           <img src="${mon.artwork}" alt="" draggable="false">
           <span class="lineup-slot-name">${mon.name}</span>`
        : `<span class="lineup-slot-num">${i + 1}</span><span class="lineup-slot-plus">+</span><span class="lineup-slot-name muted">Empty</span>`;
      if (mon) {
        tile.addEventListener("pointerdown", (e) => startDrag(e, tile, i));
      } else {
        tile.addEventListener("click", () => openPicker(i));
      }
      lineup.appendChild(tile);
    });
  }

  function slotUnder(x, y) {
    const el = document.elementFromPoint(x, y);
    return el ? el.closest(".lineup-slot") : null;
  }

  function startDrag(e, tile, index) {
    if (e.button !== undefined && e.button !== 0) return;
    drag = { index, tile, startX: e.clientX, startY: e.clientY, ghost: null, over: null, pointerId: e.pointerId };
    tile.setPointerCapture(e.pointerId);
    tile.addEventListener("pointermove", onDragMove);
    tile.addEventListener("pointerup", endDrag);
    tile.addEventListener("pointercancel", cancelDrag);
  }

  function onDragMove(e) {
    if (!drag) return;
    if (!drag.ghost) {
      // Small threshold so a plain tap/click doesn't start a drag.
      if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 6) return;
      const rect = drag.tile.getBoundingClientRect();
      drag.offsetX = drag.startX - rect.left;
      drag.offsetY = drag.startY - rect.top;
      drag.ghost = drag.tile.cloneNode(true);
      drag.ghost.classList.add("lineup-ghost");
      drag.ghost.style.width = `${rect.width}px`;
      drag.ghost.style.height = `${rect.height}px`;
      document.body.appendChild(drag.ghost);
      drag.tile.classList.add("is-dragging");
      document.body.classList.add("lineup-dragging");
    }
    e.preventDefault();
    drag.ghost.style.transform = `translate(${e.clientX - drag.offsetX}px, ${e.clientY - drag.offsetY}px)`;
    const over = slotUnder(e.clientX, e.clientY);
    if (over !== drag.over) {
      if (drag.over) drag.over.classList.remove("is-drop-target");
      drag.over = over && over !== drag.tile ? over : null;
      if (drag.over) drag.over.classList.add("is-drop-target");
    }
  }

  function cleanupDrag() {
    if (!drag) return;
    drag.tile.removeEventListener("pointermove", onDragMove);
    drag.tile.removeEventListener("pointerup", endDrag);
    drag.tile.removeEventListener("pointercancel", cancelDrag);
    if (drag.ghost) drag.ghost.remove();
    if (drag.over) drag.over.classList.remove("is-drop-target");
    drag.tile.classList.remove("is-dragging");
    document.body.classList.remove("lineup-dragging");
  }

  function endDrag() {
    if (!drag) return;
    const from = drag.index;
    const target = drag.ghost && drag.over ? parseInt(drag.over.dataset.index, 10) : null;
    cleanupDrag();
    drag = null;
    if (target === null || target === from) return;
    [teamState[from], teamState[target]] = [teamState[target], teamState[from]];
    render();
    markDirty();
  }

  function cancelDrag() {
    cleanupDrag();
    drag = null;
  }

  function render() {
    renderLineup();
    cardsContainer.innerHTML = "";
    emptyContainer.innerHTML = "";

    teamState.forEach((mon, i) => {
      if (mon) {
        cardsContainer.appendChild(renderTeamCard(mon, i));
      } else {
        const slot = document.createElement("div");
        slot.className = "team-slot empty";
        slot.innerHTML = `<button class="team-slot-add" data-index="${i}">+ Add Pokémon</button>`;
        slot.querySelector(".team-slot-add").addEventListener("click", () => openPicker(i));
        emptyContainer.appendChild(slot);
      }
    });
  }

  function openPicker(index) {
    activeSlot = index;
    modal.hidden = false;
    search.value = "";
    loadSpecies();
  }

  function closePicker() {
    modal.hidden = true;
    activeSlot = null;
  }

  closeBtn.addEventListener("click", closePicker);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closePicker();
  });

  async function loadSpecies() {
    if (allSpecies.length) {
      renderPickerGrid(allSpecies);
      return;
    }
    pickerGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/pokedex");
    if (!res.ok) {
      pickerGrid.innerHTML = "<p class='error'>Couldn't load your Pokédex.</p>";
      return;
    }
    allSpecies = await res.json();
    renderPickerGrid(allSpecies);
  }

  function renderPickerGrid(species) {
    pickerGrid.innerHTML = "";
    if (!species.length) {
      pickerGrid.innerHTML = "<p class='muted'>You haven't caught any Pokémon yet!</p>";
      return;
    }
    for (const mon of species) {
      const item = document.createElement("div");
      const mainType = (mon.types && mon.types[0]) || "normal";
      item.className = `picker-item team-picker-item tint-${mainType}`;
      item.innerHTML = `
        <img src="${mon.artwork}" alt="${mon.name}" loading="lazy">
        <div class="team-picker-name">${mon.name}${mon.has_shiny ? " ✨" : ""}</div>
        <div class="team-picker-types">${typeBadges(mon.types)}</div>`;
      item.addEventListener("click", async () => {
        // Fetch the full enriched config (moves/stats/ability) for this species
        // so the new card renders identically to one loaded from /api/team.
        const cfgRes = await fetch(`/api/proxy/pokemon-config/${mon.dex_id}`);
        const cfg = cfgRes.ok ? await cfgRes.json() : {};
        teamState[activeSlot] = {
          dex_id: mon.dex_id, name: mon.name, artwork: mon.artwork, types: mon.types,
          base_stats: cfg.base_stats || [], moves: cfg.moves || [], move_pool: cfg.move_pool || [],
          ability: cfg.ability || null, ability_raw: cfg.ability_raw || null, abilities: cfg.abilities || [],
        };
        closePicker();
        render();
        markDirty();
      });
      pickerGrid.appendChild(item);
    }
  }

  search.addEventListener("input", () => {
    const q = search.value.toLowerCase();
    renderPickerGrid(allSpecies.filter((m) => m.name.toLowerCase().includes(q) || (m.types || []).some((t) => t.startsWith(q))));
  });

  // ---------- Configure (moves + ability) modal ----------

  function openConfig(index) {
    configSlot = index;
    const mon = teamState[index];
    configSelectedMoves = (mon.moves || []).map((m) => m.name);
    configSelectedAbility = mon.ability_raw;
    configTitle.textContent = `Configure ${mon.name}`;
    configStatus.textContent = "";

    configMoves.innerHTML = "";
    for (const move of mon.move_pool || []) {
      const item = document.createElement("div");
      item.className = "picker-item move-card type-" + move.type;
      if (configSelectedMoves.includes(move.name)) item.classList.add("selected");
      item.innerHTML = `<div class="move-card-name">${move.name}</div><div class="move-card-meta">${move.type} · ${move.category}${move.pp != null ? ` · ${move.pp} PP` : ""}</div>`;
      window.MoveTooltip.attach(item, move);
      item.addEventListener("click", () => {
        if (configSelectedMoves.includes(move.name)) {
          configSelectedMoves = configSelectedMoves.filter((m) => m !== move.name);
          item.classList.remove("selected");
        } else {
          if (configSelectedMoves.length >= 4) {
            configStatus.textContent = "You can only pick up to 4 moves — remove one first.";
            return;
          }
          configSelectedMoves.push(move.name);
          item.classList.add("selected");
        }
        configStatus.textContent = "";
      });
      configMoves.appendChild(item);
    }

    configAbilities.innerHTML = "";
    for (const ability of mon.abilities || []) {
      const item = document.createElement("div");
      item.className = "picker-item";
      if (ability.name === configSelectedAbility) item.classList.add("selected");
      item.innerHTML = `<div>${ability.label}</div>`;
      item.addEventListener("click", () => {
        configSelectedAbility = ability.name;
        for (const child of configAbilities.children) child.classList.remove("selected");
        item.classList.add("selected");
      });
      configAbilities.appendChild(item);
    }

    configModal.hidden = false;
  }

  configClose.addEventListener("click", () => (configModal.hidden = true));
  configModal.addEventListener("click", (e) => {
    if (e.target === configModal) configModal.hidden = true;
  });

  configSave.addEventListener("click", async () => {
    const mon = teamState[configSlot];
    configStatus.textContent = "Saving...";
    const res = await fetch(`/api/proxy/pokemon-config/${mon.dex_id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ moves: configSelectedMoves, ability: configSelectedAbility }),
    });
    const data = await res.json();
    if (!res.ok) {
      configStatus.textContent = data.detail || "Couldn't save.";
      return;
    }
    // Re-fetch resolved config so move objects (type/category) render correctly.
    const cfgRes = await fetch(`/api/proxy/pokemon-config/${mon.dex_id}`);
    const cfg = cfgRes.ok ? await cfgRes.json() : {};
    teamState[configSlot] = { ...mon, ...cfg };
    configModal.hidden = true;
    render();
  });

  // ---------- Save team ----------

  saveBtn.addEventListener("click", async () => {
    status.textContent = "Saving...";
    status.classList.remove("error");
    const dex_ids = teamState.filter(Boolean).map((m) => m.dex_id);
    const res = await fetch("/api/proxy/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dex_ids }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = data.detail || "Couldn't save your team.";
      status.classList.add("error");
      return;
    }
    status.textContent = "Team saved!";
    saveBtn.classList.remove("is-attention");
  });

  render();
})();
