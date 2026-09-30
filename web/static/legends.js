(function () {
  const modal = document.getElementById("legend-modal");
  const closeBtn = document.getElementById("lg-close");
  const title = document.getElementById("lg-title");
  const body = document.getElementById("lg-body");
  if (!modal) return;

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });
  document.querySelectorAll("[data-legend-key]").forEach((card) =>
    card.addEventListener("click", () => openLegend(card.dataset.legendKey))
  );

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const typeBadges = (types) => (types || []).map((t) => `<span class="type-badge type-${t}">${cap(t)}</span>`).join("");

  async function openLegend(key) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch(`/api/proxy/legends/${key}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this Legend.</p>";
      return;
    }
    const l = await res.json();
    title.textContent = `${l.name} — ${l.title}`;

    // Each Pokémon, and what it Mega Evolves into.
    const roster = l.roster.map((m) => `
      <div class="gym-roster-mon${m.mega ? " has-mega" : ""}">
        <img src="${artThumb(m.artwork)}" alt="${esc(m.name)}">
        <div class="gym-roster-mon-name">${esc(m.name)}</div>
        <div class="favorite-types" style="justify-content:center;margin-top:2px;">${typeBadges(m.types)}</div>
        ${m.mega ? `<div class="legend-mega-tag" title="Mega Evolves into ${esc(m.mega.name)}">⇢ ${esc(m.mega.name)}</div>` : ""}
      </div>`).join("");

    let status;
    let actions = "";
    if (!l.unlocked) {
      status = "🔒 Defeat all 4 regional Champions to challenge the Legends.";
    } else {
      status = l.beaten
        ? `🏆 You've defeated ${esc(l.name)}${l.wins > 1 ? ` ${l.wins} times` : ""}. Rematches still pay out.`
        : `First win: <strong>${esc(l.reward.name)}</strong> with perfect IVs, plus coins and a Master Ball.`;
      actions = `
        <div class="gym-hero-actions" id="lg-actions">
          <button id="lg-fight" class="btn-primary legend-fight-btn">Challenge ${esc(l.name)}</button>
        </div>
        <p class="error" id="lg-error" hidden></p>`;
    }

    body.innerHTML = `
      <div class="gym-hero legend-hero">
        <div class="gym-hero-main">
          <img class="gym-hero-leader" src="${l.portrait}" alt="${esc(l.name)}">
        </div>
      </div>
      <div class="gym-hero-info">
        <div class="gym-hero-eyebrow">${esc(l.region)} · Legend</div>
        <h2 class="gym-hero-title">${esc(l.name)}</h2>
        <p class="muted">${esc(l.flavor)}</p>
        <div class="legend-reward-row">
          <img src="${artThumb(l.reward.artwork)}" alt="">
          <div><div class="muted">Signature reward</div><strong>${esc(l.reward.name)}</strong></div>
        </div>
        <p class="muted">${status}</p>
        ${actions}
      </div>
      <hr>
      <h3>Team <span class="muted legend-mega-count">· ${l.megas.length} Mega Evolution${l.megas.length === 1 ? "" : "s"}</span></h3>
      <div class="gym-roster-grid">${roster}</div>`;

    const fight = document.getElementById("lg-fight");
    if (fight) fight.addEventListener("click", () => confirmFight(l));
  }

  // One last "are you sure?" inside the modal (the page can't use confirm()).
  function confirmFight(l) {
    const actions = document.getElementById("lg-actions");
    actions.innerHTML = `
      <div class="legend-confirm">
        <p><strong>No turning back.</strong> ${esc(l.name)} fights at full power. Ready?</p>
        <div class="legend-confirm-buttons">
          <button id="lg-go" class="btn-primary legend-fight-btn">Let's do this</button>
          <button id="lg-back" class="btn-secondary">Not yet</button>
        </div>
      </div>`;
    document.getElementById("lg-back").addEventListener("click", () => openLegend(l.key));
    document.getElementById("lg-go").addEventListener("click", (e) => startFight(l.key, e.currentTarget));
  }

  async function startFight(key, btn) {
    btn.disabled = true;
    btn.textContent = "Starting battle...";
    const errorEl = document.getElementById("lg-error");
    try {
      const res = await fetch(`/api/proxy/battles/legend/${key}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Couldn't start this battle.");
      window.location.href = `/battles/${data.battle_id}`;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Let's do this";
      if (errorEl) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
      }
    }
  }
})();
