(function () {
  // ---------- Filters (type/category/generation/status + search) ----------

  const search = document.getElementById("dex-search");
  const typeBtn = document.getElementById("type-filter-btn");
  const typePanel = document.getElementById("type-filter-panel");
  const typeWrap = document.getElementById("type-filter-wrap");
  const typeCheckboxes = Array.from(document.querySelectorAll(".type-checkbox"));
  const rarityFilter = document.getElementById("filter-rarity");
  const genFilter = document.getElementById("filter-gen");
  const statusFilter = document.getElementById("filter-status");
  const clearBtn = document.getElementById("filter-clear");
  const emptyMsg = document.getElementById("dex-empty");
  const cards = Array.from(document.querySelectorAll("#dex-grid .dex-card"));

  function selectedTypes() {
    return typeCheckboxes.filter((cb) => cb.checked).map((cb) => cb.value);
  }

  function updateTypeButtonLabel() {
    const types = selectedTypes();
    if (types.length === 0) {
      typeBtn.textContent = "All Types";
    } else if (types.length <= 2) {
      typeBtn.textContent = types.map((t) => t.charAt(0).toUpperCase() + t.slice(1)).join(" / ");
    } else {
      typeBtn.textContent = `${types.length} types selected`;
    }
  }

  typeBtn.addEventListener("click", () => {
    typePanel.hidden = !typePanel.hidden;
  });

  document.addEventListener("click", (e) => {
    if (!typeWrap.contains(e.target)) typePanel.hidden = true;
  });

  typeCheckboxes.forEach((cb) =>
    cb.addEventListener("change", () => {
      updateTypeButtonLabel();
      applyFilters();
    })
  );

  function applyFilters() {
    const q = search.value.trim().toLowerCase();
    const types = selectedTypes();
    const rarity = rarityFilter.value;
    const gen = genFilter.value;
    const status = statusFilter.value;

    let visibleCount = 0;
    for (const card of cards) {
      const cardTypes = card.dataset.types.split(" ");
      const matchesSearch = !q || card.dataset.name.includes(q);
      const matchesType = types.length === 0 || types.every((t) => cardTypes.includes(t));
      const matchesRarity = !rarity || card.dataset.rarity === rarity;
      const matchesGen = !gen || card.dataset.gen === gen;
      const matchesStatus =
        !status ||
        (status === "owned" && card.dataset.owned === "true") ||
        (status === "unowned" && card.dataset.owned === "false");

      const visible = matchesSearch && matchesType && matchesRarity && matchesGen && matchesStatus;
      card.hidden = !visible;
      if (visible) visibleCount++;
    }
    emptyMsg.hidden = visibleCount > 0;
  }

  [search, rarityFilter, genFilter, statusFilter].forEach((el) => el.addEventListener("input", applyFilters));

  clearBtn.addEventListener("click", () => {
    search.value = "";
    typeCheckboxes.forEach((cb) => (cb.checked = false));
    updateTypeButtonLabel();
    rarityFilter.value = "";
    genFilter.value = "";
    statusFilter.value = "";
    applyFilters();
  });

  // ---------- Species detail modal ----------

  const modal = document.getElementById("species-modal");
  const closeBtn = document.getElementById("sm-close");
  const title = document.getElementById("sm-title");
  const body = document.getElementById("sm-body");

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });

  cards.forEach((card) => card.addEventListener("click", () => openSpecies(card.dataset.id)));

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function moveGrid(moves) {
    return Array.from({ length: 4 }, (_, i) => {
      const m = moves[i];
      if (!m) return `<div class="move-card" style="opacity:0.4;"><div class="move-card-name">—</div></div>`;
      return `
        <div class="move-card type-${m.type}">
          <div class="move-card-name">${m.name}</div>
          <div class="move-card-meta">${m.type} · ${m.category}${m.pp != null ? ` · ${m.pp} PP` : ""}</div>
        </div>`;
    }).join("");
  }

  function statBars(stats) {
    return stats
      .map(
        (s) => `
        <div class="stat-bar-row">
            <span class="stat-bar-label">${s.label}</span>
            <div class="stat-bar-track"><div class="stat-bar-fill" style="width:${Math.min(100, (s.at_level_100 / 400) * 100)}%"></div></div>
            <span class="stat-bar-value">${s.at_level_100}</span>
        </div>`
      )
      .join("");
  }

  async function openSpecies(dexId) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/species/${dexId}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this Pokémon.</p>";
      return;
    }
    const mon = await res.json();
    title.textContent = `#${String(mon.dex_id).padStart(3, "0")} · ${mon.name}`;

    let statusLine;
    if (mon.count > 0) {
      statusLine = `You currently have ${mon.count}.`;
    } else if (mon.owned) {
      statusLine = "You've caught this species before, but don't currently have one (evolved, traded, or released).";
    } else {
      statusLine = "You haven't caught this species yet — it can appear in the wild.";
    }

    let evoHtml = "";
    if (mon.evolution) {
      const pct = Math.min(100, (mon.evolution.have_candy / mon.evolution.needed_candy) * 100);
      evoHtml = `
        <div class="evolution-box">
            <div class="section-label">Evolution</div>
            <div>${mon.evolution.candidates.map((c) => `→ <strong>${c.name}</strong>`).join(", ")}</div>
            <div class="xp-bar" style="margin: 8px 0 4px;"><div class="xp-bar-fill" style="width:${pct}%"></div></div>
            <div class="muted">${mon.evolution.have_candy} / ${mon.evolution.needed_candy} ${mon.evolution.family_candy_label}</div>
        </div>`;
    }

    body.innerHTML = `
        <div class="collection-detail">
            <div class="collection-detail-art">
                <div class="favorite-circle"><img src="${mon.artwork}" alt="${mon.name}"></div>
            </div>
            <div class="collection-detail-info">
                <h2 style="margin: 4px 0;">${mon.name}</h2>
                <div class="favorite-types" style="justify-content: flex-start; margin: 8px 0;">${typeBadges(mon.types)}</div>
                <p class="muted">${statusLine}</p>
                ${evoHtml}
            </div>
        </div>
        <hr>
        <h3>Combat Configuration</h3>
        <div class="move-grid">${moveGrid(mon.moves)}</div>
        <div style="margin-top: 10px;"><span class="ability-badge">${mon.ability || "No ability set"}</span></div>
        <hr>
        <h3>Base Stats · Level 100</h3>
        <div class="stat-bars">${statBars(mon.base_stats)}</div>
        ${mon.count > 0 ? `<hr><a href="/collection" class="btn-secondary">Manage in Collection</a>` : ""}
    `;

    body.querySelectorAll(".move-grid .move-card").forEach((cell, i) => {
      if (mon.moves[i]) window.MoveTooltip.attach(cell, mon.moves[i]);
    });
  }
})();
