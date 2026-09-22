(function () {
  const modal = document.getElementById("gym-modal");
  const closeBtn = document.getElementById("gm-close");
  const title = document.getElementById("gm-title");
  const body = document.getElementById("gm-body");

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });

  document.querySelectorAll(".gym-card").forEach((card) =>
    card.addEventListener("click", () => openGym(card.dataset.gymKey))
  );

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  async function openGym(gymKey) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/gyms/${gymKey}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this gym.</p>";
      return;
    }
    const gym = await res.json();
    title.textContent = `${gym.location} Gym`;

    const rosterHtml = gym.roster
      .map(
        (mon) => `
        <div class="gym-roster-mon">
            <img src="${mon.artwork}" alt="${mon.name}">
            <div class="gym-roster-mon-name">${mon.name}</div>
            <div class="favorite-types" style="justify-content:center;margin-top:2px;">${typeBadges(mon.types)}</div>
        </div>`
      )
      .join("");

    const typeLabel = gym.type_theme.charAt(0).toUpperCase() + gym.type_theme.slice(1);
    let statusLine;
    let actionsHtml = "";
    if (gym.earned) {
      statusLine = "✅ You've earned this badge.";
    } else if (gym.is_next) {
      statusLine = "This is your next gym — challenge it from Discord or right here on the web.";
      actionsHtml = `
        <div class="gym-hero-actions">
            <button id="gm-fight-btn" class="btn-primary">Fight ${gym.leader_name}</button>
        </div>
        <p class="error" id="gm-fight-error" hidden></p>`;
    } else {
      statusLine = "🔒 Beat the earlier gyms first to unlock this challenge.";
    }

    body.innerHTML = `
        <div class="gym-hero">
            <span class="type-badge type-${gym.type_theme} gym-hero-type-tag">${typeLabel}</span>
            <div class="gym-hero-main">
                <img class="gym-hero-leader" src="${gym.leader_image}" alt="${gym.leader_name}">
                <div class="gym-hero-badge-showcase">
                    <img class="gym-hero-badge-img" src="${gym.badge_image}" alt="${gym.badge_name}">
                    <div class="gym-hero-badge-label">Gym Badge</div>
                    <div class="gym-hero-badge-name">${gym.badge_name}</div>
                </div>
            </div>
        </div>
        <div class="gym-hero-info">
            <div class="gym-hero-eyebrow">${gym.location} · ${typeLabel}-type Gym</div>
            <h2 class="gym-hero-title">${gym.leader_name}</h2>
            <p class="muted">${gym.flavor}</p>
            <p class="muted">${statusLine}</p>
            ${actionsHtml}
        </div>
        <hr>
        <h3>Gym Team</h3>
        <div class="gym-roster-grid">${rosterHtml}</div>
    `;

    const fightBtn = document.getElementById("gm-fight-btn");
    if (fightBtn) {
      fightBtn.addEventListener("click", () => startGymFight(gymKey, fightBtn));
    }
  }

  async function startGymFight(gymKey, btn) {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Starting battle...";
    const errorEl = document.getElementById("gm-fight-error");

    try {
      const res = await fetch(`/api/proxy/battles/gym/${gymKey}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Couldn't start this battle.");
      }
      window.location.href = `/battles/${data.battle_id}`;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = originalLabel;
      if (errorEl) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
      }
    }
  }
})();
