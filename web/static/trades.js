(function () {
  const PAGE_SIZE = 12;

  const pickFriendBtn = document.getElementById("tp-pick-friend");
  const pickMineBtn = document.getElementById("tp-pick-mine");
  const pickTheirsBtn = document.getElementById("tp-pick-theirs");
  const sendBtn = document.getElementById("tp-send");
  const tpError = document.getElementById("tp-error");
  const listEl = document.getElementById("trade-proposals-list");
  const refreshLink = document.getElementById("trades-refresh");

  let friend = null; // {user_id, username, character_sprite}
  let myPick = null; // {id, dex_id, name, nickname, artwork, is_shiny, iv_percent}
  let theirPick = null;

  function setError(msg) {
    if (!msg) {
      tpError.hidden = true;
      return;
    }
    tpError.hidden = false;
    tpError.textContent = msg;
  }

  function updateSlot(btn, pick, placeholder) {
    const text = btn.querySelector(".trade-propose-slot-text");
    if (!pick) {
      btn.classList.remove("is-filled");
      text.textContent = placeholder;
      return;
    }
    btn.classList.add("is-filled");
    const sprite = pick.character_sprite || pick.artwork;
    const label = pick.username || pick.nickname || pick.name;
    text.innerHTML = `<img src="${sprite}" alt=""><span>${label}${pick.is_shiny ? " ✨" : ""}</span>`;
  }

  function refreshSendState() {
    pickTheirsBtn.disabled = !friend;
    sendBtn.disabled = !(friend && myPick && theirPick);
  }

  // ---------- Friend picker ----------

  const friendModal = document.getElementById("friend-picker-modal");
  const friendClose = document.getElementById("fp-close");
  const friendSearch = document.getElementById("fp-search");
  const friendGrid = document.getElementById("fp-grid");
  let allTrainers = [];

  friendClose.addEventListener("click", () => (friendModal.hidden = true));
  friendModal.addEventListener("click", (e) => {
    if (e.target === friendModal) friendModal.hidden = true;
  });
  friendSearch.addEventListener("input", () => renderFriendGrid(filterTrainers(friendSearch.value)));

  function filterTrainers(query) {
    const q = query.trim().toLowerCase();
    if (!q) return allTrainers;
    const mentionMatch = q.match(/^<@!?(\d+)>$/);
    if (mentionMatch) {
      const uid = mentionMatch[1];
      return allTrainers.filter((t) => String(t.user_id) === uid);
    }
    return allTrainers.filter((t) => t.username.toLowerCase().includes(q));
  }

  async function openFriendPicker() {
    friendModal.hidden = false;
    friendSearch.value = "";
    friendGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/trainers");
    if (!res.ok) {
      friendGrid.innerHTML = "<p class='error'>Couldn't load trainers.</p>";
      return;
    }
    const trainers = await res.json();
    allTrainers = trainers.filter((t) => !t.is_you);
    renderFriendGrid(allTrainers);
  }

  function renderFriendGrid(trainers) {
    friendGrid.innerHTML = "";
    if (!trainers.length) {
      friendGrid.innerHTML = "<p class='muted'>No trainers found.</p>";
      return;
    }
    for (const t of trainers) {
      const item = document.createElement("div");
      item.className = "picker-item trainer-picker-item";
      item.innerHTML = `
        <img src="${t.character_sprite}" alt="${t.username}">
        <div>${t.username}</div>
        <div class="muted">Level ${t.level}</div>`;
      item.addEventListener("click", () => {
        friend = t;
        theirPick = null;
        updateSlot(pickFriendBtn, friend, "Choose a friend");
        updateSlot(pickTheirsBtn, null, "Choose their Pokémon");
        refreshSendState();
        friendModal.hidden = true;
      });
      friendGrid.appendChild(item);
    }
  }

  pickFriendBtn.addEventListener("click", openFriendPicker);

  // ---------- Pokémon picker (generic: 'mine' or 'theirs') ----------

  const pokeModal = document.getElementById("pokemon-picker-modal");
  const pokeTitle = document.getElementById("pp-title");
  const pokeClose = document.getElementById("pp-close");
  const pokeSearch = document.getElementById("pp-search");
  const speciesView = document.getElementById("pp-species-view");
  const speciesGrid = document.getElementById("pp-species-grid");
  const pagination = document.getElementById("pp-pagination");
  const pageLabel = document.getElementById("pp-page-label");
  const prevBtn = document.getElementById("pp-prev");
  const nextBtn = document.getElementById("pp-next");
  const individualsView = document.getElementById("pp-individuals-view");
  const individualsGrid = document.getElementById("pp-individuals-grid");
  const backBtn = document.getElementById("pp-back");

  let pokeMode = "mine";
  let allSpecies = [];
  let currentPage = 0;

  pokeClose.addEventListener("click", () => (pokeModal.hidden = true));
  pokeModal.addEventListener("click", (e) => {
    if (e.target === pokeModal) pokeModal.hidden = true;
  });
  backBtn.addEventListener("click", () => {
    individualsView.hidden = true;
    speciesView.hidden = false;
  });
  pokeSearch.addEventListener("input", () => {
    currentPage = 0;
    renderSpeciesPage();
  });
  prevBtn.addEventListener("click", () => {
    if (currentPage > 0) {
      currentPage--;
      renderSpeciesPage();
    }
  });
  nextBtn.addEventListener("click", () => {
    currentPage++;
    renderSpeciesPage();
  });

  function speciesEndpoint() {
    return pokeMode === "mine" ? "/api/proxy/collection" : `/api/proxy/trainer/${friend.user_id}/collection`;
  }

  function individualsEndpoint(dexId) {
    return pokeMode === "mine"
      ? `/api/proxy/collection/by-species/${dexId}`
      : `/api/proxy/trainer/${friend.user_id}/collection/by-species/${dexId}`;
  }

  async function openPokemonPicker(mode) {
    pokeMode = mode;
    pokeTitle.textContent = mode === "mine" ? "Choose your Pokémon" : `Choose ${friend.username}'s Pokémon`;
    pokeModal.hidden = false;
    pokeSearch.value = "";
    currentPage = 0;
    speciesView.hidden = false;
    individualsView.hidden = true;
    speciesGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch(speciesEndpoint());
    if (!res.ok) {
      speciesGrid.innerHTML = "<p class='error'>Couldn't load that collection.</p>";
      return;
    }
    allSpecies = await res.json();
    renderSpeciesPage();
  }

  function renderSpeciesPage() {
    const q = pokeSearch.value.trim().toLowerCase();
    const filtered = q ? allSpecies.filter((m) => m.name.toLowerCase().includes(q)) : allSpecies;
    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    currentPage = Math.min(currentPage, totalPages - 1);
    const pageItems = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

    speciesGrid.innerHTML = "";
    if (!pageItems.length) {
      speciesGrid.innerHTML = "<p class='muted'>No Pokémon found.</p>";
    }
    for (const mon of pageItems) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.innerHTML = `<img src="${mon.artwork}" alt="${mon.name}">
        <div>${mon.name}${mon.is_shiny ? " ✨" : ""}</div>
        <div class="muted">${mon.count > 1 ? `${mon.count} owned &middot; ` : ""}best IV ${mon.best_iv_percent}%</div>`;
      item.addEventListener("click", () => {
        if (mon.count <= 1) {
          choosePick({ ...mon });
          return;
        }
        openIndividuals(mon.dex_id);
      });
      speciesGrid.appendChild(item);
    }

    pagination.hidden = filtered.length <= PAGE_SIZE;
    pageLabel.textContent = `Page ${currentPage + 1} of ${totalPages}`;
    prevBtn.disabled = currentPage === 0;
    nextBtn.disabled = currentPage >= totalPages - 1;
  }

  async function openIndividuals(dexId) {
    speciesView.hidden = true;
    individualsView.hidden = false;
    individualsGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch(individualsEndpoint(dexId));
    if (!res.ok) {
      individualsGrid.innerHTML = "<p class='error'>Couldn't load those Pokémon.</p>";
      return;
    }
    const individuals = await res.json();
    individualsGrid.innerHTML = "";
    for (const mon of individuals) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.innerHTML = `<img src="${mon.artwork}" alt="${mon.name}">
        <div>${mon.nickname || mon.name}${mon.is_shiny ? " ✨" : ""}</div>
        <div class="muted">IV ${mon.iv_percent}%</div>`;
      item.addEventListener("click", () => choosePick(mon));
      individualsGrid.appendChild(item);
    }
  }

  function choosePick(mon) {
    if (pokeMode === "mine") {
      myPick = mon;
      updateSlot(pickMineBtn, myPick, "Choose your Pokémon");
    } else {
      theirPick = mon;
      updateSlot(pickTheirsBtn, theirPick, "Choose their Pokémon");
    }
    refreshSendState();
    pokeModal.hidden = true;
  }

  pickMineBtn.addEventListener("click", () => openPokemonPicker("mine"));
  pickTheirsBtn.addEventListener("click", () => {
    if (!friend) return;
    openPokemonPicker("theirs");
  });

  // ---------- Review + send ----------

  const reviewModal = document.getElementById("review-modal");
  const reviewClose = document.getElementById("rv-close");
  const reviewBody = document.getElementById("rv-body");
  reviewClose.addEventListener("click", () => (reviewModal.hidden = true));
  reviewModal.addEventListener("click", (e) => {
    if (e.target === reviewModal) reviewModal.hidden = true;
  });

  sendBtn.addEventListener("click", () => {
    setError(null);
    reviewModal.hidden = false;
    reviewBody.innerHTML = `
        <p class="muted">Proposing a trade with <strong>${friend.username}</strong>:</p>
        <div class="trade-review-columns">
            <div class="trade-review-col">
                <div class="trade-propose-label">You Give</div>
                <img src="${myPick.artwork}" alt="${myPick.name}">
                <div>${myPick.nickname || myPick.name}${myPick.is_shiny ? " ✨" : ""}</div>
            </div>
            <div class="trade-swap-icon">⇄</div>
            <div class="trade-review-col">
                <div class="trade-propose-label">You Receive</div>
                <img src="${theirPick.artwork}" alt="${theirPick.name}">
                <div>${theirPick.nickname || theirPick.name}${theirPick.is_shiny ? " ✨" : ""}</div>
            </div>
        </div>
        <button id="rv-confirm" class="btn-primary" style="margin-top:16px;">Send Trade</button>
        <p class="error" id="rv-error" hidden></p>
    `;
    document.getElementById("rv-confirm").addEventListener("click", sendProposal);
  });

  async function sendProposal() {
    const confirmBtn = document.getElementById("rv-confirm");
    const rvError = document.getElementById("rv-error");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Sending...";
    try {
      const res = await fetch("/api/proxy/trades/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_user_id: friend.user_id,
          offer_catch_id: myPick.id,
          request_catch_id: theirPick.id,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Couldn't send this trade.");
      reviewModal.hidden = true;
      friend = null;
      myPick = null;
      theirPick = null;
      updateSlot(pickFriendBtn, null, "Choose a friend");
      updateSlot(pickMineBtn, null, "Choose your Pokémon");
      updateSlot(pickTheirsBtn, null, "Choose their Pokémon");
      refreshSendState();
      loadTrades();
    } catch (err) {
      rvError.hidden = false;
      rvError.textContent = err.message;
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Send Trade";
    }
  }

  // ---------- Trade card modal (view a specific individual's full stats) ----------

  const cardModal = document.getElementById("trade-card-modal");
  const cardClose = document.getElementById("tc-close");
  const cardTitle = document.getElementById("tc-title");
  const cardBody = document.getElementById("tc-body");
  cardClose.addEventListener("click", () => (cardModal.hidden = true));
  cardModal.addEventListener("click", (e) => {
    if (e.target === cardModal) cardModal.hidden = true;
  });

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function statBars(stats) {
    return stats
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

  function openCard(mon) {
    cardModal.hidden = false;
    cardTitle.textContent = mon.nickname || mon.name;
    cardBody.innerHTML = `
        <div class="collection-detail">
            <div class="collection-detail-art">
                <div class="favorite-circle"><img src="${mon.artwork}" alt="${mon.name}"></div>
            </div>
            <div class="collection-detail-info">
                <div class="muted">#${String(mon.dex_id).padStart(3, "0")} &middot; IV Quality ${mon.iv_percent}%</div>
                <h2 style="margin: 4px 0;">${mon.nickname || mon.name}${mon.is_shiny ? " ✨" : ""}</h2>
                ${mon.nickname ? `<div class="muted">${mon.name}</div>` : ""}
                <div class="favorite-types" style="justify-content: flex-start; margin: 8px 0;">${typeBadges(mon.types)}</div>
            </div>
        </div>
        <hr>
        <h3>Stats &middot; Level 100</h3>
        <div class="stat-bars">${statBars(mon.base_stats)}</div>
    `;
  }

  // ---------- Trades list ----------

  async function postAction(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(data.detail || "That action couldn't be completed.");
      return null;
    }
    return data;
  }

  function tradeCard(t) {
    const you = t.you || {};
    const leftName = t.name_a;
    const rightName = t.name_b;
    const leftLabel = you.side === "A" ? "You Give" : you.side === "B" ? "You Receive" : `${leftName} gives`;
    const rightLabel = you.side === "B" ? "You Give" : you.side === "A" ? "You Receive" : `${rightName} gives`;

    const monHtml = (mon, label) => {
      if (!mon) return `<div class="trade-review-col"><div class="trade-propose-label">${label}</div><p class="muted">Not chosen yet</p></div>`;
      return `
        <div class="trade-review-col trade-card-mon" data-mon='${JSON.stringify(mon).replace(/'/g, "&apos;")}'>
            <div class="trade-propose-label">${label}</div>
            <img src="${mon.artwork}" alt="${mon.name}">
            <div>${mon.nickname || mon.name}${mon.is_shiny ? " ✨" : ""}</div>
            <div class="muted">IV ${mon.iv_percent}%</div>
        </div>`;
    };

    let statusLabel;
    let statusClass = "";
    if (t.status === "pending") { statusLabel = "Awaiting response"; statusClass = "is-pending"; }
    else if (t.status === "active") { statusLabel = "Active"; statusClass = "is-pending"; }
    else if (t.status === "completed") { statusLabel = "Completed"; statusClass = "is-completed"; }
    else if (t.status === "declined") { statusLabel = "Declined"; statusClass = "is-declined"; }
    else { statusLabel = "Cancelled"; statusClass = "is-declined"; }

    let actionsHtml = "";
    if (you.is_pending_target) {
      actionsHtml = `
        <button class="btn-primary trade-accept-btn" data-id="${t.trade_id}">Accept</button>
        <button class="btn-secondary trade-decline-btn" data-id="${t.trade_id}">Decline</button>`;
    } else if (you.can_cancel) {
      actionsHtml = `<button class="btn-secondary trade-cancel-btn" data-id="${t.trade_id}">Cancel</button>`;
    }

    return `
      <div class="card trade-proposal-card">
          <div class="trade-proposal-header">
              <span class="trade-status-tag ${statusClass}">${statusLabel}</span>
              <span class="muted">${leftName} ⇄ ${rightName}</span>
          </div>
          <div class="trade-review-columns">
              ${monHtml(t.mon_a, leftLabel)}
              <div class="trade-swap-icon">⇄</div>
              ${monHtml(t.mon_b, rightLabel)}
          </div>
          <div class="trade-proposal-footer">
              <span class="muted">Created ${new Date(t.created_at).toLocaleString()}</span>
              <div class="trade-proposal-actions">${actionsHtml}</div>
          </div>
      </div>`;
  }

  async function loadTrades() {
    const res = await fetch("/api/proxy/trades");
    if (!res.ok) {
      listEl.innerHTML = "<p class='error'>Couldn't load your trades.</p>";
      return;
    }
    const data = await res.json();
    const all = [...(data.live || []), ...(data.recent || [])];
    if (!all.length) {
      listEl.innerHTML = "<p class='muted'>No trades yet — propose one above.</p>";
      return;
    }
    listEl.innerHTML = all.map(tradeCard).join("");

    listEl.querySelectorAll(".trade-accept-btn").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await postAction(`/api/proxy/trades/${btn.dataset.id}/accept`);
        loadTrades();
      })
    );
    listEl.querySelectorAll(".trade-decline-btn").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await postAction(`/api/proxy/trades/${btn.dataset.id}/decline`);
        loadTrades();
      })
    );
    listEl.querySelectorAll(".trade-cancel-btn").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("Cancel this trade?")) return;
        await postAction(`/api/proxy/trades/${btn.dataset.id}/cancel`);
        loadTrades();
      })
    );
    listEl.querySelectorAll(".trade-card-mon").forEach((el) =>
      el.addEventListener("click", () => openCard(JSON.parse(el.dataset.mon.replace(/&apos;/g, "'"))))
    );
  }

  refreshLink.addEventListener("click", (e) => {
    e.preventDefault();
    loadTrades();
  });

  loadTrades();
})();
