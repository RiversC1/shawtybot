(function () {
  const search = document.getElementById("collection-search");
  const cards = Array.from(document.querySelectorAll("#collection-grid .collection-card"));
  const emptyMsg = document.getElementById("collection-empty");

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    let visible = 0;
    for (const card of cards) {
      const match = !q || card.dataset.name.includes(q);
      card.hidden = !match;
      if (match) visible++;
    }
    emptyMsg.hidden = visible > 0;
  });

  cards.forEach((card) => card.addEventListener("click", () => openDetail(card.dataset.id)));

  // ---------- Detail modal ----------

  const modal = document.getElementById("collection-modal");
  const closeBtn = document.getElementById("cm-close");
  const title = document.getElementById("cm-title");
  const body = document.getElementById("cm-body");

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function moveGrid(moves) {
    const cells = Array.from({ length: 4 }, (_, i) => {
      const m = moves[i];
      if (!m) return `<div class="move-card" style="opacity:0.4;"><div class="move-card-name">—</div></div>`;
      return `
        <div class="move-card type-${m.type}">
          <div class="move-card-name">${m.name}</div>
          <div class="move-card-meta">${m.type} · ${m.category}${m.pp != null ? ` · ${m.pp} PP` : ""}</div>
        </div>`;
    });
    return cells.join("");
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

  async function openDetail(catchId) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/collection/${catchId}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this Pokémon.</p>";
      return;
    }
    const mon = await res.json();
    title.textContent = mon.nickname || mon.name;

    const caughtDate = new Date(mon.caught_at).toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric",
    });

    let evoTeaser = "";
    if (mon.evolution) {
      const pct = Math.min(100, (mon.evolution.have_candy / mon.evolution.needed_candy) * 100);
      evoTeaser = `
        <div class="evolution-box">
            <div class="section-label">Evolution</div>
            <div>${mon.evolution.candidates.map((c) => `→ <strong>${c.name}</strong>`).join(", ")}</div>
            <div class="xp-bar" style="margin: 8px 0 4px;"><div class="xp-bar-fill" style="width:${pct}%"></div></div>
            <div class="muted">${mon.evolution.have_candy} / ${mon.evolution.needed_candy} ${mon.evolution.family_candy_label}</div>
            <button id="cm-view-evolution" class="btn-secondary" style="margin-top: 8px;">View Evolution ⋄</button>
        </div>`;
    }

    body.innerHTML = `
            <div class="collection-detail">
                <div class="collection-detail-art">
                    <div class="favorite-circle"><img src="${mon.artwork}" alt="${mon.name}"></div>
                </div>
                <div class="collection-detail-info">
                    <div class="muted">#${String(mon.dex_id).padStart(3, "0")} · Your catch${mon.count > 1 ? ` · You have ${mon.count}` : ""}</div>
                    <h2 style="margin: 4px 0;">${mon.nickname || mon.name}${mon.is_shiny ? " ✨" : ""}</h2>
                    ${mon.nickname ? `<div class="muted">${mon.name}</div>` : ""}
                    <div class="favorite-types" style="justify-content: flex-start; margin: 8px 0;">${typeBadges(mon.types)}</div>
                    ${evoTeaser}
                </div>
            </div>
            <hr>
            <h3>Combat Configuration</h3>
            <div class="move-grid">${moveGrid(mon.moves)}</div>
            <div style="margin-top: 10px;"><span class="ability-badge">${mon.ability || "No ability set"}</span></div>
            <hr>
            <h3>Base Stats · Level 100</h3>
            <div class="stat-bars">${statBars(mon.base_stats)}</div>
            <hr>
            <p class="muted">With you since ${caughtDate}. Encountered in Discord.</p>
            <div class="collection-actions">
                <button id="cm-add-team" class="btn-primary">Add to Team +</button>
                <a href="/pokedex" class="btn-secondary">View in Pokédex</a>
                <button id="cm-rename" class="btn-secondary">Rename</button>
            </div>
            <div id="cm-rename-row" hidden style="margin-top: 10px; display: flex; gap: 8px;">
                <input type="text" id="cm-nickname-input" class="search-input" placeholder="New nickname..." maxlength="32" value="${mon.nickname || ""}">
                <button id="cm-nickname-save" class="btn-primary">Save</button>
            </div>
            <p id="cm-status" class="muted"></p>
        `;

    body.querySelectorAll(".move-grid .move-card").forEach((cell, i) => {
      if (mon.moves[i]) window.MoveTooltip.attach(cell, mon.moves[i]);
    });

    const evoBtn = document.getElementById("cm-view-evolution");
    if (evoBtn) evoBtn.addEventListener("click", () => openEvolution(catchId));

    document.getElementById("cm-add-team").addEventListener("click", async () => {
      const status = document.getElementById("cm-status");
      status.textContent = "Adding to team...";
      const teamRes = await fetch("/api/proxy/team");
      const currentTeam = teamRes.ok ? await teamRes.json() : [];
      const dexIds = currentTeam.map((m) => m.dex_id);
      if (dexIds.length >= 6) {
        status.textContent = "Your team is already full (6/6). Remove one on the Team page first.";
        status.classList.add("error");
        return;
      }
      dexIds.push(mon.dex_id);
      const saveRes = await fetch("/api/proxy/team", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dex_ids: dexIds }),
      });
      const data = await saveRes.json();
      status.classList.toggle("error", !saveRes.ok);
      status.textContent = saveRes.ok ? `Added ${mon.name} to your team!` : data.detail || "Couldn't add to team.";
    });

    document.getElementById("cm-rename").addEventListener("click", () => {
      document.getElementById("cm-rename-row").hidden = false;
    });

    document.getElementById("cm-nickname-save").addEventListener("click", async () => {
      const status = document.getElementById("cm-status");
      const nickname = document.getElementById("cm-nickname-input").value.trim();
      const res2 = await fetch(`/api/proxy/collection/${catchId}/nickname`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname: nickname || null }),
      });
      const data = await res2.json();
      if (!res2.ok) {
        status.classList.add("error");
        status.textContent = data.detail || "Couldn't rename.";
        return;
      }
      status.classList.remove("error");
      status.textContent = "Nickname saved! Refresh the page to see it everywhere.";
      title.textContent = nickname || mon.name;
    });
  }

  // ---------- Evolution modal ----------

  const evoModal = document.getElementById("evolution-modal");
  const evoClose = document.getElementById("em-close");
  const evoTitle = document.getElementById("em-title");
  const evoBody = document.getElementById("em-body");

  evoClose.addEventListener("click", () => (evoModal.hidden = true));
  evoModal.addEventListener("click", (e) => {
    if (e.target === evoModal) evoModal.hidden = true;
  });

  async function openEvolution(catchId) {
    modal.hidden = true;
    evoModal.hidden = false;
    await renderEvolution(catchId);
  }

  async function renderEvolution(catchId) {
    evoTitle.textContent = "Loading...";
    evoBody.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/collection/${catchId}`);
    if (!res.ok) {
      evoBody.innerHTML = "<p class='error'>Couldn't load this Pokémon.</p>";
      return;
    }
    const mon = await res.json();
    evoTitle.textContent = `Evolution of ${mon.nickname || mon.name}`;

    if (!mon.evolution) {
      evoBody.innerHTML = `<p class="muted">${mon.name} doesn't evolve any further.</p>`;
      return;
    }

    const evo = mon.evolution;
    const shortfall = Math.max(0, evo.needed_candy - evo.have_candy);

    const invRes = await fetch("/api/proxy/inventory");
    const inv = invRes.ok ? await invRes.json() : { rare_candy: 0 };
    const rareCandy = inv.rare_candy || 0;
    const convertMax = Math.min(shortfall, rareCandy);

    const candidatesHtml = evo.candidates
      .map((c) => {
        const missingItem = c.item_needed && !c.has_item;
        const canEvolveThis = !missingItem && shortfall === 0;
        let statusHtml;
        if (missingItem) {
          statusHtml = `<div class="error">Needs ${c.item_needed} — check the Store</div>`;
        } else if (shortfall > 0) {
          statusHtml = `<div class="muted">Needs ${shortfall} more ${evo.family_candy_label}</div>`;
        } else {
          statusHtml = `<div style="color: var(--yellow);">Requirements complete</div>`;
        }
        return `
          <div class="card" style="margin-top: 12px;">
              <div style="display:flex; align-items:center; gap:16px; flex-wrap: wrap;">
                  <img src="${c.artwork}" alt="${c.name}" style="width:80px; height:80px; object-fit:contain; image-rendering:pixelated;">
                  <div style="flex:1; min-width: 160px;">
                      <h3 style="margin: 0 0 4px;">${c.name}</h3>
                      <div class="muted">${EVOLUTION_CANDY_COST_LABEL(evo)}</div>
                      ${statusHtml}
                  </div>
              </div>
              <button class="btn-primary evolve-btn" data-target="${c.dex_id}" style="margin-top: 10px;" ${canEvolveThis ? "" : "disabled"}>
                  Evolve to ${c.name}
              </button>
          </div>`;
      })
      .join("");

    let convertHtml = "";
    if (shortfall > 0) {
      if (convertMax > 0) {
        convertHtml = `
        <div class="card" style="margin-top: 12px;">
            <p class="muted">Convert Rare Candy into ${evo.family_candy_label} — 1 Rare Candy = 1 ${evo.family_candy_label}. You need ${shortfall} more.</p>
            <div style="display:flex; gap:8px; align-items:center;">
                <input type="number" id="convert-amount" class="store-qty" min="1" max="${convertMax}" value="${convertMax}">
                <button id="convert-btn" class="btn-secondary">Convert Rare Candy</button>
            </div>
        </div>`;
      } else {
        convertHtml = `
        <div class="card" style="margin-top: 12px;">
            <p class="muted">You need ${shortfall} more ${evo.family_candy_label}, but you don't have any Rare Candy to convert. Catch more ${mon.name} or earn Rare Candy from achievements and coffers.</p>
        </div>`;
      }
    }

    evoBody.innerHTML = `
        <p class="muted">You keep this Pokémon and its shiny status — you're just choosing its next form.</p>
        <p class="muted">You have <strong>${evo.have_candy}</strong> ${evo.family_candy_label} and <strong>${rareCandy}</strong> Rare Candy.</p>
        ${candidatesHtml}
        ${convertHtml}
        <p id="evo-status" class="muted"></p>
    `;

    evoBody.querySelectorAll(".evolve-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Evolve ${mon.nickname || mon.name} into this form? This can't be undone.`)) return;
        const status = document.getElementById("evo-status");
        status.textContent = "Evolving...";
        status.classList.remove("error");
        const res2 = await fetch(`/api/proxy/collection/${catchId}/evolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_dex_id: parseInt(btn.dataset.target, 10) }),
        });
        const data = await res2.json();
        if (!res2.ok) {
          status.classList.add("error");
          status.textContent = data.detail || "Couldn't evolve.";
          return;
        }
        status.textContent = `Evolved into ${data.new_name}! Reloading...`;
        setTimeout(() => window.location.reload(), 1200);
      });
    });

    const convertBtn = document.getElementById("convert-btn");
    if (convertBtn) {
      convertBtn.addEventListener("click", async () => {
        const amountInput = document.getElementById("convert-amount");
        const amount = parseInt(amountInput.value, 10) || 0;
        const status = document.getElementById("evo-status");
        status.textContent = "Converting...";
        status.classList.remove("error");
        const res2 = await fetch(`/api/proxy/collection/${catchId}/convert-candy`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount }),
        });
        const data = await res2.json();
        if (!res2.ok) {
          status.classList.add("error");
          status.textContent = data.detail || "Couldn't convert candy.";
          return;
        }
        status.textContent = "Converted! Updating...";
        await renderEvolution(catchId);
      });
    }
  }

  function EVOLUTION_CANDY_COST_LABEL(evo) {
    return `${evo.needed_candy} ${evo.family_candy_label} required`;
  }
})();
