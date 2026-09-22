(function () {
  const POLL_INTERVAL_MS = 2000;
  const WS_CONNECT_TIMEOUT_MS = 3000;

  const titleEl = document.getElementById("tr-title");
  const subtitleEl = document.getElementById("tr-subtitle");
  const resultEl = document.getElementById("tr-result");
  const nameAEl = document.getElementById("tr-name-a");
  const nameBEl = document.getElementById("tr-name-b");
  const slotA = document.getElementById("tr-slot-a");
  const slotB = document.getElementById("tr-slot-b");
  const actionPanel = document.getElementById("tr-action-panel");
  const connectionStatusEl = document.getElementById("tr-connection-status");
  const cancelBtn = document.getElementById("tr-cancel-btn");

  let current = JSON.parse(document.getElementById("trade-data").textContent);
  let pollTimer = null;
  let ws = null;
  let wsConnectTimer = null;
  let usingWs = false;

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

  // ---------- Card modal (data already in the trade payload, no fetch needed) ----------

  const cardModal = document.getElementById("trade-card-modal");
  const cardClose = document.getElementById("tc-close");
  const cardTitle = document.getElementById("tc-title");
  const cardBody = document.getElementById("tc-body");
  cardClose.addEventListener("click", () => (cardModal.hidden = true));
  cardModal.addEventListener("click", (e) => {
    if (e.target === cardModal) cardModal.hidden = true;
  });

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

  // ---------- Offer picker (species -> individuals -> POST offer) ----------

  const pickerModal = document.getElementById("trade-picker-modal");
  const pickerClose = document.getElementById("tp-close");
  const pickerSearch = document.getElementById("tp-search");
  const speciesView = document.getElementById("tp-species-view");
  const speciesGrid = document.getElementById("tp-species-grid");
  const individualsView = document.getElementById("tp-individuals-view");
  const individualsGrid = document.getElementById("tp-individuals-grid");
  const backBtn = document.getElementById("tp-back");

  let allSpecies = [];

  pickerClose.addEventListener("click", () => (pickerModal.hidden = true));
  pickerModal.addEventListener("click", (e) => {
    if (e.target === pickerModal) pickerModal.hidden = true;
  });
  backBtn.addEventListener("click", () => {
    individualsView.hidden = true;
    speciesView.hidden = false;
  });
  pickerSearch.addEventListener("input", () => {
    const q = pickerSearch.value.trim().toLowerCase();
    renderSpeciesGrid(allSpecies.filter((m) => !q || m.name.toLowerCase().includes(q)));
  });

  async function openPicker() {
    pickerModal.hidden = false;
    speciesView.hidden = false;
    individualsView.hidden = true;
    pickerSearch.value = "";
    speciesGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/collection");
    if (!res.ok) {
      speciesGrid.innerHTML = "<p class='error'>Couldn't load your collection.</p>";
      return;
    }
    allSpecies = await res.json();
    renderSpeciesGrid(allSpecies);
  }

  function renderSpeciesGrid(species) {
    speciesGrid.innerHTML = "";
    if (!species.length) {
      speciesGrid.innerHTML = "<p class='muted'>You haven't caught any Pokémon yet!</p>";
      return;
    }
    for (const mon of species) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.innerHTML = `<img src="${mon.artwork}" alt="${mon.name}">
        <div>${mon.name}${mon.is_shiny ? " ✨" : ""}</div>
        <div class="muted">${mon.count > 1 ? `${mon.count} owned &middot; ` : ""}best IV ${mon.best_iv_percent}%</div>`;
      item.addEventListener("click", async () => {
        if (mon.count <= 1) {
          await submitOffer(mon.id);
          return;
        }
        await openIndividuals(mon.dex_id);
      });
      speciesGrid.appendChild(item);
    }
  }

  async function openIndividuals(dexId) {
    speciesView.hidden = true;
    individualsView.hidden = false;
    individualsGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch(`/api/proxy/collection/by-species/${dexId}`);
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
      item.addEventListener("click", () => submitOffer(mon.id));
      individualsGrid.appendChild(item);
    }
  }

  async function submitOffer(catchId) {
    const data = await postAction(`/api/proxy/trades/${window.TRADE_ID}/offer`, { catch_id: catchId });
    pickerModal.hidden = true;
    if (data) applyUpdate(data);
  }

  // ---------- Rendering ----------

  function renderSlot(slotEl, mon, side, isYourSide, tradeStatus) {
    if (!mon) {
      slotEl.innerHTML = isYourSide && tradeStatus === "active"
        ? `<div class="trade-offer-empty"><p class="muted">No Pokémon selected yet</p>
             <button class="btn-primary trade-choose-btn">Choose Pokémon</button></div>`
        : `<div class="trade-offer-empty"><p class="muted">Waiting for a pick...</p></div>`;
      const btn = slotEl.querySelector(".trade-choose-btn");
      if (btn) btn.addEventListener("click", openPicker);
      return;
    }
    slotEl.innerHTML = `
        <div class="trade-offer-card">
            <img src="${mon.artwork}" alt="${mon.name}">
            <div class="trade-offer-name">${mon.nickname || mon.name}${mon.is_shiny ? " ✨" : ""}</div>
            <div class="muted">IV Quality ${mon.iv_percent}%</div>
            ${isYourSide && tradeStatus === "active" ? `<button class="btn-secondary trade-change-btn">Change</button>` : ""}
        </div>`;
    slotEl.querySelector(".trade-offer-card").addEventListener("click", (e) => {
      if (e.target.closest(".trade-change-btn")) return;
      openCard(mon);
    });
    const changeBtn = slotEl.querySelector(".trade-change-btn");
    if (changeBtn) changeBtn.addEventListener("click", (e) => { e.stopPropagation(); openPicker(); });
  }

  function renderActionPanel(trade) {
    const you = trade.you;
    cancelBtn.hidden = true;

    if (trade.status === "completed" || trade.status === "declined" || trade.status === "cancelled") {
      actionPanel.hidden = true;
      return;
    }

    if (!you || !you.side) {
      actionPanel.hidden = true;
      return;
    }

    if (trade.status === "pending") {
      const oppName = you.side === "A" ? trade.name_b : trade.name_a;
      if (you.is_pending_target) {
        actionPanel.hidden = false;
        actionPanel.innerHTML = `
            <p>${oppName} wants to trade with you!</p>
            <div class="battle-action-buttons">
                <button id="tr-accept" class="btn-primary">Accept</button>
                <button id="tr-decline" class="btn-secondary">Decline</button>
            </div>`;
        document.getElementById("tr-accept").addEventListener("click", async () => {
          const data = await postAction(`/api/proxy/trades/${window.TRADE_ID}/accept`);
          if (data) applyUpdate(data);
        });
        document.getElementById("tr-decline").addEventListener("click", async () => {
          await postAction(`/api/proxy/trades/${window.TRADE_ID}/decline`);
          location.reload();
        });
      } else {
        actionPanel.hidden = false;
        actionPanel.innerHTML = `<p class="muted">Waiting for ${oppName} to accept your trade invite...</p>`;
      }
      return;
    }

    // active
    cancelBtn.hidden = false;
    const myConfirmed = you.side === "A" ? trade.confirmed_a : trade.confirmed_b;
    const myMon = you.side === "A" ? trade.mon_a : trade.mon_b;
    actionPanel.hidden = false;
    if (myConfirmed) {
      actionPanel.innerHTML = `<p class="muted">✅ You're ready — waiting on the other trainer to confirm.</p>`;
    } else {
      actionPanel.innerHTML = `
          <p class="muted">Pick a Pokémon above, then confirm when you're happy with the trade.</p>
          <button id="tr-confirm" class="btn-primary" ${myMon ? "" : "disabled"}>Confirm Trade</button>`;
      const confirmBtn = document.getElementById("tr-confirm");
      if (confirmBtn) {
        confirmBtn.addEventListener("click", async () => {
          const data = await postAction(`/api/proxy/trades/${window.TRADE_ID}/confirm`);
          if (data) applyUpdate(data);
        });
      }
    }
  }

  function render(trade) {
    current = trade;
    titleEl.textContent = `${trade.name_a} ⇄ ${trade.name_b}`;
    subtitleEl.textContent =
      trade.status === "completed" ? "Trade completed"
      : trade.status === "declined" ? "Trade declined"
      : trade.status === "cancelled" ? "Trade cancelled"
      : trade.status === "pending" ? "Awaiting response"
      : "Choosing Pokémon";

    nameAEl.textContent = trade.name_a;
    nameBEl.textContent = trade.name_b;

    const you = trade.you || {};
    renderSlot(slotA, trade.mon_a, "A", you.side === "A", trade.status);
    renderSlot(slotB, trade.mon_b, "B", you.side === "B", trade.status);

    if (trade.status === "completed") {
      resultEl.hidden = false;
      resultEl.textContent = `✅ Trade complete! ${trade.name_a} gave ${trade.mon_a ? (trade.mon_a.nickname || trade.mon_a.name) : "a Pokémon"}, ${trade.name_b} gave ${trade.mon_b ? (trade.mon_b.nickname || trade.mon_b.name) : "a Pokémon"}.`;
    } else if (trade.status === "declined") {
      resultEl.hidden = false;
      resultEl.textContent = "This trade was declined.";
    } else if (trade.status === "cancelled") {
      resultEl.hidden = false;
      resultEl.textContent = "This trade was cancelled.";
    } else {
      resultEl.hidden = true;
    }

    renderActionPanel(trade);
  }

  function applyUpdate(trade) {
    render(trade);
  }

  async function poll() {
    try {
      const res = await fetch(`/api/proxy/trades/${window.TRADE_ID}`);
      if (!res.ok) return;
      applyUpdate(await res.json());
    } catch (e) {
      // transient network hiccup — next tick retries
    }
  }

  function startPolling() {
    if (pollTimer) return;
    connectionStatusEl.textContent = "Live (polling)";
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}/ws/trades/${window.TRADE_ID}`);
    ws = socket;

    wsConnectTimer = setTimeout(() => {
      if (!usingWs) {
        socket.close();
        startPolling();
      }
    }, WS_CONNECT_TIMEOUT_MS);

    socket.addEventListener("message", (event) => {
      usingWs = true;
      clearTimeout(wsConnectTimer);
      stopPolling();
      connectionStatusEl.textContent = "Live";
      try {
        applyUpdate(JSON.parse(event.data));
      } catch (e) {
        // ignore malformed frame
      }
    });

    socket.addEventListener("close", () => {
      if (["completed", "declined", "cancelled"].includes(current.status)) return;
      usingWs = false;
      startPolling();
      setTimeout(connectWs, 4000);
    });

    socket.addEventListener("error", () => {
      // "close" fires right after — handled there.
    });
  }

  render(current);
  if (!["completed", "declined", "cancelled"].includes(current.status)) {
    connectWs();
  } else {
    connectionStatusEl.textContent = "Finished";
  }

  cancelBtn.addEventListener("click", async () => {
    if (!confirm("Cancel this trade?")) return;
    const data = await postAction(`/api/proxy/trades/${window.TRADE_ID}/cancel`);
    if (data) applyUpdate(data);
  });
})();
